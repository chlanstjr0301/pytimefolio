"""orders: 주문을 넣고, 정지하고, 끝날 때까지 추적한다.

근거 (보고서 3장, 4장):
- 운영사: 하루 100건 이상이면 IP 차단 가능, 몰아치는 요청 금지 -> 하루 80건(신규+정지), 쓰기 간격 1 s.
- AddOrder 응답에 주문 ID 가 없다 -> 피드의 ords 에서 새로 생긴 주문을 찾아 ID 를 얻는다.
- 응답을 못 받은 주문은 바로 다시 보내지 않는다 -> 30 s 기다렸다 피드에서 찾고, 없을 때만 한 번 더.
- 주문의 wei 는 목표 비중이 아니라 주문 크기(%) -> target_to_orders 가 (목표 - 현재)로 바꾼다.
- 끝난 주문은 모두 state=Done. cnclT 가 있으면 정지, 0 < cumQty < qty2ord 이면 일부 체결.
- ModifyOrder/DeleteOrder 는 실측 전이라 넣지 않는다.
- 매도는 ls=L, ex=X 로 보내고 서버 기록은 sgn=-1 (2026-11-24 09:43 실측).
- qty2ord 는 (wei% x NAV / 가격)의 내림: 삼성 0.1% -> 4주, LIG 0.1% -> 2주. 1주 비중보다 작은 주문은 0주가 될 수 있다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import fcntl
import json
import os
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import AmbiguousState, BlockedError, RequestError, ServerError, TimefolioError
from .hub import Hub
from .rest import Rest, KST

log = logging.getLogger("pytimefolio.orders")
STATE_DIR = Path.home() / ".config" / "pytimefolio"
WEI_STEP = 0.01          # 주문 크기 단위(%). 서버가 비중을 소수 둘째 자리로 다룬다 (보유 비중 표시 기준)


def quantize(wei: float) -> float:
    """주문 크기를 WEI_STEP 단위로 맞춘다. 보내는 값과 피드에서 찾는 값이 같아지도록 한 번만 한다."""
    return round(round(wei / WEI_STEP) * WEI_STEP, 2)


# ---------------------------------------------------------------- 가격 도구
def tick_size(price: float) -> int:
    """KRX 주식 호가 단위 (2023년 개편 기준)."""
    for bound, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < bound:
            return tick
    return 1_000


def round_to_tick(price: float, down: bool = True) -> int:
    t = tick_size(price)
    return int((math.floor if down else math.ceil)(price / t) * t)


# ---------------------------------------------------------------- 주문 명세
@dataclass
class Order:
    prod_id: str
    wei: float                         # 주문 크기 (% of NAV), 양수
    exit: bool = False                 # False = 진입/매수(ex=E), True = 청산/매도(ex=X)
    limit_prc: int | None = None       # 지정가
    limit_idx: int | None = None       # 상대호가 n (1~10), 자기호가는 -n
    stop_prc: int | None = None
    hm0: str | None = None             # None 이면 지금(즉시 실행)
    hm1: str | None = None             # None 이면 즉시 실행, 있으면 시간 분할
    exit_all: bool = False

    def __post_init__(self):
        self.wei = quantize(self.wei)
        if self.wei <= 0:
            raise ValueError(f"{self.prod_id}: 주문 크기가 {WEI_STEP}% 보다 작음")

    def body(self, d: str, pf_id: int, now_hm: str) -> dict:
        if sum(x is not None for x in (self.limit_prc, self.limit_idx, self.stop_prc)) != 1:
            raise ValueError("limit_prc, limit_idx, stop_prc 중 정확히 하나를 지정해야 함")
        return {"d": d, "pfId": pf_id, "prodId": self.prod_id, "ls": "L", "ex": "X" if self.exit else "E",
                "wei": self.wei, "exitAll": self.exit_all, "limitPrc": self.limit_prc,
                "limitIdx": self.limit_idx, "stopPrc": self.stop_prc,
                "hm0": self.hm0 or now_hm, "hm1": self.hm1}


@dataclass
class Result:
    order_id: int
    outcome: str                       # "filled", "stopped", "partly_filled_then_stopped"
    cum_qty: int
    qty2ord: int
    avg_px: float | None
    record: dict = field(repr=False, default_factory=dict)


def default_stock_cap(prod_id: str) -> float:
    """보유하지 않은 종목의 종목 한도 (대회 가이드라인: 삼성전자 40%, 그 밖 15%)."""
    return 40.0 if prod_id == "A005930" else 15.0


def _same_order(rec: dict, o: "Order") -> bool:
    return (rec.get("prodId") == o.prod_id and rec.get("ex") == ("X" if o.exit else "E")
            and abs(float(rec.get("wei") or 0) - o.wei) < WEI_STEP / 2)


def _parse_utc(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def remaining_wei(rec: dict) -> float:
    """체결 중인 주문의 남은 크기(%). 매수 +, 매도 -."""
    wei = float(rec.get("wei") or 0)
    q, cum = int(rec.get("qty2ord") or 0), int(rec.get("cumQty") or 0)
    left = wei * (1 - cum / q) if q else wei
    return -left if rec.get("ex") == "X" else left


# ---------------------------------------------------------------- 목표 비중 -> 주문
def cap_weights(targets: dict[str, float], caps: dict[str, float]) -> dict[str, float]:
    """종목 한도를 넘는 비중을 잘라 나머지 종목에 비례 배분한다 (water-filling). 합계는 유지."""
    w = {k: max(0.0, v) for k, v in targets.items()}
    for _ in range(len(w)):
        over = {k: v - caps[k] for k, v in w.items() if k in caps and v > caps[k] + 1e-12}
        if not over:
            break
        excess = sum(over.values())
        for k in over:
            w[k] = caps[k]
        free = {k: v for k, v in w.items() if (k not in caps or v < caps[k] - 1e-12) and v > 0}
        tot = sum(free.values())
        if tot <= 0:
            break                       # 받을 종목이 없으면 남는 비중은 현금으로
        for k, v in free.items():
            w[k] = v + excess * v / tot
    return w


def sector_violations(targets: dict[str, float], sec_of: dict[str, str], mkt: dict[str, float],
                      floor: float = 10.0, mult: float = 2.0) -> dict[str, tuple[float, float]]:
    """목표 비중의 섹터 합이 max(시장 섹터 비중 x mult, floor) 를 넘는 섹터 -> (합계, 한도).
    섹터를 모르는 종목이 있으면 KeyError (추측하지 않는다)."""
    tot: dict[str, float] = {}
    for prod, w in targets.items():
        if w > 0:
            tot[sec_of[prod]] = tot.get(sec_of[prod], 0.0) + w
    out = {}
    for sec, w in tot.items():
        lim = max(mult * mkt.get(sec, 0.0), floor)
        if w > lim + 1e-9:
            out[sec] = (round(w, 4), round(lim, 4))
    return out


def target_to_orders(targets: dict[str, float], current: dict[str, float], min_wei: float = 0.05,
                     pending: dict[str, float] | None = None, **price) -> list[Order]:
    """목표 비중(%)과 현재 비중(%)의 차이를 주문으로 바꾼다. 차이가 min_wei 보다 작으면 건너뛴다.
    pending: 아직 체결 중인 주문의 남은 크기(%, 매수 +, 매도 -). 이미 나가 있는 몫은 다시 주문하지 않는다.
    price 는 Order 의 가격·시간 인자 (예: limit_idx=5, hm0="09:00", hm1="09:30")."""
    pending = pending or {}
    out = []
    for prod in sorted(set(targets) | set(current) | set(pending)):
        delta = targets.get(prod, 0.0) - current.get(prod, 0.0) - pending.get(prod, 0.0)
        if abs(delta) >= max(min_wei, WEI_STEP):
            out.append(Order(prod, abs(delta), exit=delta < 0, **price))
    # 매도를 먼저 내야 현금이 생긴다
    return sorted(out, key=lambda o: not o.exit)


# ---------------------------------------------------------------- 주문 게이트
class Orders:
    def __init__(self, rest: Rest, hub: Hub, daily_cap: int = 80, min_interval: float = 1.0):
        self.r, self.hub = rest, hub
        self.daily_cap, self.min_interval = daily_cap, min_interval
        self._write_lock = asyncio.Lock()
        self._place_lock = asyncio.Lock()        # 넣기와 찾기를 한 묶음으로: 같은 주문 두 건이 같은 ID 를 잡지 않게
        self._last_write = 0.0
        self._claimed: set[int] = set()         # place() 가 이미 돌려준 ID: 같은 내용의 다음 주문이 가져가지 못하게
        self.halted: str | None = None           # 403/429 를 받으면 이유가 들어가고 이후 쓰기를 모두 거부

    # ---- 하루 건수 (재시작해도 이어진다)
    def _count_file(self) -> Path:
        return STATE_DIR / f"orders_{self.r.biz_day}.json"

    def _update_count(self, fn) -> int:
        """건수 파일을 잠그고 읽고 바꾸고 원자적으로 쓴다. 여러 프로세스가 동시에 써도 깨지지 않는다."""
        f = self._count_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f.with_suffix(".lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            n = json.loads(f.read_text())["n"] if f.exists() else 0
            n = fn(n)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"n": n}))
            os.replace(tmp, f)
        return n

    def used_today(self) -> int:
        f = self._count_file()
        try:
            return json.loads(f.read_text())["n"] if f.exists() else 0
        except ValueError:
            return self.daily_cap                 # 읽을 수 없으면 한도를 다 쓴 것으로 본다

    def sync_count(self, server_count: int) -> int:
        """서버의 오늘 주문 기록(신규 1건 + 정지된 주문마다 1건)으로 로컬 건수를 맞춘다. 큰 값을 쓴다."""
        return self._update_count(lambda n: max(n, server_count))

    def _bump(self, k: int = 1) -> None:
        self._update_count(lambda n: n + k)

    # ---- 쓰기 요청 하나 (간격 1 s, 건수 기록)
    async def _write(self, path: str, body: dict, count: int = 1):
        """count: 이 요청이 하루 건수에 더하는 수 (신규 1, 정지는 정지한 주문 수)."""
        async with self._write_lock:
            self.halted = self.halted or self.r.a.t.blocked   # 읽기·피드에서 받은 403/429 도 쓰기를 멈춘다
            if self.halted:
                raise BlockedError(f"쓰기 중단 상태: {self.halted}")
            if self.used_today() + count > self.daily_cap:
                raise TimefolioError(f"하루 주문 한도 {self.daily_cap}건 초과 (사용 {self.used_today()}, 요청 {count})")
            wait = self._last_write + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            sent = True
            try:
                return await self.r.a.call("POST", f"/api/Portfolio/{path}", json=body)
            except BlockedError as e:
                self.halted = str(e)
                log.error("차단 신호 -> 이후 모든 쓰기 중단: %s", e)
                raise
            except ServerError as e:
                sent = e.sent
                raise
            finally:
                self._last_write = time.monotonic()
                if sent:
                    self._bump(count)                 # 응답을 못 받았어도 서버에 갔을 수 있으므로 센다

    def _now_hm(self) -> str:
        return dt.datetime.fromtimestamp(self.r.a.t.server_time(), KST).strftime("%H:%M")

    # ---- 사전 점검
    async def check(self, o: Order) -> None:
        self.hub.assert_fresh()                       # 오래된 상태로 주문하지 않는다
        if not self.r.biz_day_is_today():
            raise AmbiguousState(f"서버 영업일 {self.r.biz_day} != 오늘 {self.r.kst_today()}: 주문하지 않음")
        warns = await self.r.validate_product(o.prod_id, entry=not o.exit)
        if warns:
            raise RequestError(f"{o.prod_id} ValidateProduct 경고: {warns}")
        pos = self.hub.state.by_prod().get(o.prod_id) or {}
        if not o.exit:
            lim = float(pos.get("weiLimit") or default_stock_cap(o.prod_id))
            if float(pos.get("wei") or 0) + o.wei > lim:
                raise RequestError(f"{o.prod_id} 종목 한도 {lim}% 초과")

    # ---- 피드에서 새 주문 찾기
    @staticmethod
    def _one(o: Order, cands: list[tuple[int, dict]]) -> dict | None:
        """새로 나타난 같은 내용의 주문이 둘 이상이면 어느 것이 이 주문인지 알 수 없으므로 고르지 않고 멈춘다."""
        if len(cands) > 1:
            ids = ", ".join(str(i) for i, _ in cands)
            raise AmbiguousState(f"{o.prod_id} {o.wei}% 와 같은 내용의 새 주문이 여러 건 있음 (Id: {ids}). "
                                 "래퍼는 어느 것이 방금 넣은 주문인지 구별할 수 없음. "
                                 "다시 보내지 않았으니 화면에서 확인할 것")
        return cands[0][1] if cands else None

    def _match(self, o: Order, before: set) -> dict | None:
        cands = [(oid, rec) for oid, rec in self.hub.state.ords.items()
                 if oid not in before and _same_order(rec, o)]
        return self._one(o, cands)

    async def _find_rest(self, o: Order, before: set, since_utc: dt.datetime) -> dict | None:
        """REST Orders(서버 기록)에서 찾는다. 피드가 끊겼을 때도 쓸 수 있는 확인 수단."""
        cands = []
        for rec in await self.r.orders():
            if rec.get("Id") in before or not _same_order(rec, o):
                continue
            g = _parse_utc(rec.get("genT"))
            if g is None or g >= since_utc - dt.timedelta(seconds=5):
                cands.append((rec.get("Id"), rec))
        return self._one(o, cands)

    async def _find(self, o: Order, before: set, timeout: float) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            # 처음 일치한 순간 피드에 보이는 후보만으로 판단한다. 같은 내용의 두 번째 기록이
            # 그 직후(다음 3 s 갱신)에야 나타나면 여기서는 알아채지 못하고 첫 기록을 돌려준다.
            rec = self._match(o, before)
            if rec:
                return rec
            await asyncio.sleep(0.5)                   # 피드는 3 s 마다 갱신된다
        return None

    # ---- 주문
    async def place(self, o: Order) -> int:
        """주문을 넣고 주문 ID 를 돌려준다. ID 는 피드에서 찾고, 못 찾으면 REST Orders 로 확인한다.
        응답을 못 받은 주문은 자동으로 다시 보내지 않는다 (서버에 들어갔는지 확인할 수 없으면 멈춘다)."""
        async with self._place_lock:
            await self.check(o)
            body = o.body(self.r.biz_day, self.r._pf(), self._now_hm())
            before = set(self.hub.state.ords) | self._claimed
            since = dt.datetime.fromtimestamp(self.r.a.t.server_time(), dt.timezone.utc)
            try:
                await self._write("AddOrder", body)
            except ServerError as e:
                if not e.sent:                         # 서버에 닿지 않은 것이 확실할 때만 한 번 다시
                    log.warning("AddOrder 연결 실패 (%s) -> 한 번 다시 보냄", e)
                    await asyncio.sleep(2)
                    try:
                        await self._write("AddOrder", body)
                    except ServerError as e2:              # 다시 보낸 것도 응답이 없으면 찾기로 넘어간다 (세 번째는 없음)
                        log.warning("다시 보낸 AddOrder 도 실패 (%s) -> 피드와 Orders 에서 확인", e2)
                else:
                    log.warning("AddOrder 응답 없음 (%s) -> 피드와 Orders 에서 확인, 다시 보내지 않음", e)
            rec = await self._find(o, before, 15)
            if not rec:
                rec = await self._find_rest(o, before, since)
            if not rec:
                raise AmbiguousState(f"{o.prod_id} {o.wei}% 주문이 서버에 있는지 확인하지 못함. "
                                     "다시 보내지 않았으니 화면이나 Orders 로 확인할 것")
            self._claimed.add(rec["Id"])
            log.info("주문 접수 Id=%s %s %.2f%% state=%s qty2ord=%s",
                     rec["Id"], o.prod_id, o.wei, rec.get("state"), rec.get("qty2ord"))
            return rec["Id"]

    async def stop(self, ids: list[int]) -> None:
        """주문 정지 (화면의 '정지'). 이미 끝난 주문은 빼고 보낸다."""
        live = [i for i in ids if self.hub.state.ords.get(i, {}).get("state") != "Done"]
        if live:
            await self._write("CancelOrderBatch", {"d": self.r.biz_day, "pfId": self.r._pf(),
                                                  "ords": ",".join(map(str, live))}, count=len(live))

    async def wait_done(self, oid: int, timeout: float | None = None) -> Result:
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            self.hub.assert_fresh()                   # 피드가 죽었으면 끝없이 기다리지 않는다
            rec = self.hub.state.ords.get(oid, {})
            if rec.get("state") == "Done":
                cum, q = int(rec.get("cumQty") or 0), int(rec.get("qty2ord") or 0)
                if rec.get("cnclT") is None:
                    outcome = "filled"
                elif cum > 0:
                    outcome = "partly_filled_then_stopped"
                else:
                    outcome = "stopped"
                return Result(oid, outcome, cum, q, rec.get("avgPx"), rec)
            if end is not None and time.monotonic() > end:
                raise asyncio.TimeoutError(f"주문 {oid} 가 {timeout} s 안에 끝나지 않음 (state={rec.get('state')})")
            await asyncio.sleep(0.5)
