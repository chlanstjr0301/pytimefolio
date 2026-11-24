"""Timefolio: 전략 코드가 쓰는 진입점. 연결, 인증, 피드, 주문을 하나로 묶는다.

    async with Timefolio(ctst_id=91) as tf:
        print(tf.weights())
        ids = await tf.rebalance({"A005930": 10.0}, limit_idx=5, hm0="09:00", hm1="09:30")
        results = [await tf.wait(i) for i in ids]
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from pathlib import Path

from .auth import Auth, DEFAULT_SESSION
from .errors import RebalanceError, TimefolioError
from .hub import Hub
from .orders import (Order, Orders, Result, cap_weights, default_stock_cap, remaining_wei,
                     sector_violations, target_to_orders)
from .rest import Rest
from .state import PortfolioState
from .transport import Transport

log = logging.getLogger("pytimefolio")


class Timefolio:
    def __init__(self, ctst_id: int, email: str | None = None, password: str | None = None,
                 session_file: Path | str = DEFAULT_SESSION, daily_cap: int = 80,
                 feed_interval: float = 3.0):
        self.ctst_id = ctst_id
        self.t = Transport()
        self.auth = Auth(self.t, email, password, session_file)
        self.rest = Rest(self.auth)
        self._daily_cap, self._interval = daily_cap, feed_interval
        self.hub: Hub | None = None
        self.orders: Orders | None = None
        self._feed_task: asyncio.Task | None = None
        self._first = asyncio.Event()
        self.sec_of: dict[str, str] = {}             # 종목코드 -> GICS 섹터 (Universe)

    # ---------------------------------------------------------------- 수명
    async def __aenter__(self) -> "Timefolio":
        try:
            return await self._start()
        except BaseException:
            await self.__aexit__(None, None, None)   # 시작하다 실패해도 피드 태스크와 연결을 닫는다
            raise

    async def _start(self) -> "Timefolio":
        await self.auth.start()
        pf = self.rest.select_portfolio(self.ctst_id)
        self.hub = Hub(self.auth, pf, interval=self._interval)
        self.orders = Orders(self.rest, self.hub, self._daily_cap)
        self._feed_task = asyncio.create_task(self._feed(), name="pytimefolio-feed")
        await asyncio.wait_for(self._first.wait(), 30)
        self.sec_of = {u["Id"]: str(u["sec"]) for u in await self.rest.universe()}
        n = await self._sync_count()
        log.info("준비 완료: pfId=%s, 영업일 %s, 오늘 주문 %d/%d건", pf, self.rest.biz_day, n, self._daily_cap)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._feed_task:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.hub:
            await self.hub.close()
        await self.t.aclose()

    async def _feed(self) -> None:
        async for _kind, _st in self.hub.stream():
            self._first.set()

    async def _sync_count(self) -> int:
        """오늘 서버에 남은 주문(신규 + 정지)을 세서 하루 한도 계산에 반영한다."""
        ords = await self.rest.orders()
        server = len(ords) + sum(1 for o in ords if o.get("cnclT"))
        return self.orders.sync_count(server)

    def _alive(self) -> None:
        """피드 태스크가 끝났거나 마지막 응답이 오래됐으면 예외. 상태를 읽고 주문하기 전에 부른다."""
        if self._feed_task and self._feed_task.done():
            exc = None if self._feed_task.cancelled() else self._feed_task.exception()
            raise TimefolioError(f"피드가 멈춤: {exc!r}")
        self.hub.assert_fresh()

    # ---------------------------------------------------------------- 상태
    @property
    def state(self) -> PortfolioState:
        self._alive()
        return self.hub.state

    def weights(self) -> dict[str, float]:
        """종목코드 -> 현재 비중(%)."""
        return {p: float(r.get("wei") or 0) for p, r in self.state.by_prod().items()}

    def positions(self) -> dict[str, dict]:
        return self.state.by_prod()

    def open_orders(self) -> list[dict]:
        return self.state.open_orders()

    def orders_left(self) -> int:
        return self._daily_cap - self.orders.used_today()

    # ---------------------------------------------------------------- 조회
    async def universe(self) -> list[dict]:
        return await self.rest.universe()

    async def session(self) -> dict:
        return await self.rest.session()

    # ---------------------------------------------------------------- 주문
    async def buy(self, prod_id: str, wei: float, **price) -> int:
        """wei: 주문 크기(%). price: limit_prc= / limit_idx= / stop_prc=, hm0=, hm1="""
        self._alive()
        return await self.orders.place(Order(prod_id, wei, exit=False, **price))

    async def sell(self, prod_id: str, wei: float, **price) -> int:
        self._alive()
        return await self.orders.place(Order(prod_id, wei, exit=True, **price))

    async def stop(self, *order_ids: int) -> None:
        await self.orders.stop(list(order_ids))

    async def wait(self, order_id: int, timeout: float | None = None) -> Result:
        return await self.orders.wait_done(order_id, timeout)

    # ---------------------------------------------------------------- 1주의 비중
    def nav(self) -> float:
        """NAV(원). 피드 bs.na 를 쓰고, 없으면 보유 종목의 평가액 / 비중 중앙값으로 역산한다.
        (2026-11-24 실측: bs.na 837,002,427 / 역산 836,798,658, 차이 0.02%)"""
        na = (self.state.bs or {}).get("na")
        if isinstance(na, (int, float)) and na > 0:
            return float(na)
        ests = [float(r["pos"]) * float(r["close"]) * 100 / float(r["wei"])
                for r in self.positions().values() if r.get("pos") and r.get("close") and r.get("wei")]
        if not ests:
            raise TimefolioError("보유 종목이 없어 NAV 를 역산할 수 없음")
        return statistics.median(ests)

    def share_weight(self, prod_id: str, price: float | None = None) -> float | None:
        """1주가 NAV 의 몇 % 인가. 보유 종목은 피드 가격, 새 종목은 호출자가 준 가격으로 계산."""
        r = self.positions().get(prod_id)
        px = price if price is not None else (float(r["close"]) if r and r.get("close") else None)
        return None if px is None else px * 100 / self.nav()

    def plan(self, targets: dict[str, float], min_wei: float = 0.05, max_total: float = 100.0,
             prices: dict[str, float] | None = None, **price) -> list[Order]:
        """목표 비중(%) -> 주문 목록 (보내지 않는다). 보유 종목의 weiLimit 으로 먼저 자른다.
        목표 합계가 max_total(%)을 넘거나, 섹터 합계가 max(시장 비중 x 2, 10%)를 넘거나,
        유니버스에 없는 종목이 있으면 거부한다. 한도로 잘린 비중은 다른 종목에 나눠지므로 합계는 그대로다."""
        total = sum(max(0.0, v) for v in targets.values())
        if total > max_total + 1e-9:
            raise ValueError(f"목표 비중 합계 {total:.2f}% > {max_total}%")
        held = self.positions()
        caps = {p: float(held.get(p, {}).get("weiLimit") or default_stock_cap(p)) for p in targets}
        capped = cap_weights(targets, caps)
        # 섹터 한도: max(시장 섹터 비중 x 2, 10%). 넘으면 주문을 만들지 않는다
        unknown = [p for p, w in capped.items() if w > 0 and p not in self.sec_of]
        if unknown:
            raise ValueError(f"투자 유니버스에 없는 종목: {unknown}")
        mkt = {str(v.get("sec")): float(v.get("wei") or 0) for v in self.state.msecs.values() if isinstance(v, dict)}
        bad = sector_violations(capped, self.sec_of, mkt)
        if bad:
            raise ValueError("섹터 한도 초과 (합계, 한도): " + ", ".join(f"{k} {w}% > {lim}%" for k, (w, lim) in bad.items()))
        pending: dict[str, float] = {}               # 체결 중인 주문의 남은 몫은 다시 주문하지 않는다
        for rec in self.open_orders():
            pending[rec["prodId"]] = pending.get(rec["prodId"], 0.0) + remaining_wei(rec)
        orders = target_to_orders(capped, self.weights(), min_wei=min_wei, pending=pending, **price)
        # 서버는 주식 수를 내림하므로 1주 비중보다 작은 주문은 0주가 된다 -> 미리 뺀다
        kept, self.dropped = [], []
        for o in orders:
            sw = self.share_weight(o.prod_id, (prices or {}).get(o.prod_id))
            if sw is None:
                log.warning("%s: 가격을 몰라 1주 비중을 확인하지 못함 (prices= 로 넘기면 확인)", o.prod_id)
            elif o.wei < sw:
                log.warning("%s: 주문 %.3f%% < 1주 %.3f%% -> 0주가 되므로 뺌", o.prod_id, o.wei, sw)
                self.dropped.append(o)
                continue
            kept.append(o)
        return kept

    # ---------------------------------------------------------------- 가이드라인
    async def guidelines(self) -> dict:
        """대회 가이드라인 현황. 피드 값으로 계산하고, 서버의 Violations 응답도 함께 돌려준다.
        - 종목: weiLimit 이하 (삼성전자 40, 그 밖 15)
        - 섹터: max(시장 섹터 비중 x 2, 10%) 이하
        - 시총 1조 미만(SmallCap) 합계: 30% 이하
        - 주간 회전율: 매주 5% 이상 (cmpl.currWto)
        ok 는 서버가 센 현재 위반이 0건이고 이번 주 회전율이 5% 이상일 때만 True."""
        pos = self.positions()
        stock = sorted(((p, float(r.get("wei") or 0), float(r.get("weiLimit") or 15)) for p, r in pos.items()),
                       key=lambda x: x[2] - x[1])
        pf_sec: dict[str, float] = {}
        for r in pos.values():
            pf_sec[str(r.get("sec"))] = pf_sec.get(str(r.get("sec")), 0.0) + float(r.get("wei") or 0)
        mkt_sec = {str(v.get("sec")): float(v.get("wei") or 0) for v in self.state.msecs.values()
                   if isinstance(v, dict)}               # msecs 레코드: {"Id": 1, "sec": "10", "wei": 0.92}
        sector = {s: (w, max(2 * mkt_sec.get(s, 0.0), 10.0)) for s, w in sorted(pf_sec.items(), key=lambda x: -x[1])}
        tags = self.state.extra.get("tags") or {}
        small = sum(float(pos[p].get("wei") or 0) for p, tg in tags.items() if p in pos and "SmallCap" in (tg or []))
        cmpl = self.state.extra.get("cmpl") or {}
        return {
            "stock": [{"prodId": p, "wei": w, "limit": lim, "headroom": round(lim - w, 2)} for p, w, lim in stock[:5]],
            "sector": {s: {"wei": round(w, 2), "limit": round(lim, 2), "headroom": round(lim - w, 2)} for s, (w, lim) in sector.items()},
            "small_cap": {"wei": round(small, 2), "limit": 30.0},
            "turnover": {"this_week": cmpl.get("currWto"), "avg": cmpl.get("avgWto"), "min": 5.0,
                         "violations": cmpl.get("wtoViols"), "expected_violations": cmpl.get("expWtoViols")},
            "server": (srv := await self.rest.violations()),
            "ok": not (srv.get("Current") or {}).get("TotalViolCnt") and (cmpl.get("currWto") or 0) >= 5.0,
        }

    async def rebalance(self, targets: dict[str, float], min_wei: float = 0.05, **price) -> list[int]:
        """plan() 의 주문을 1 s 간격으로 보내고 주문 ID 목록을 돌려준다. 한도가 모자라면 시작하지 않는다."""
        await self._sync_count()                     # 브라우저에서 넣은 주문까지 다시 센다
        orders = self.plan(targets, min_wei, **price)
        if len(orders) > self.orders_left():
            raise TimefolioError(f"주문 {len(orders)}건 > 남은 한도 {self.orders_left()}건")
        ids: list[int] = []
        for o in orders:
            try:
                ids.append(await self.orders.place(o))
            except Exception as e:
                raise RebalanceError(f"{o.prod_id} 주문 실패: {e}", ids) from e
        return ids
