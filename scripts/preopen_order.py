"""
장 시작 전 주문 실험: 지정 시각(기본 08:30)에 주문 1건을 넣고, 09:15까지 주문·보유 변화를 기록한다.

확인하려는 것
  1. 장 시작 전에 주문이 접수되는가 (AddOrder 응답, 주문 state)
  2. hm0=09:00으로 지정하면 09:00에 체결이 시작되는가
  3. wei가 목표 비중인가, 증분인가 (서버가 계산한 qty2ord와 체결 후 비중)
  4. 주문 state 값이 어떤 순서로 바뀌는가

재시도 정책 (최대 1회)
  401 → 토큰 갱신 후 즉시 1회 / 400 → 재시도 안 함(형식 오류)
  200·5xx·타임아웃 → 09:00:20에 실시간 주문 목록을 확인해, 오늘 이 종목의 에러 없는 주문이 없을 때만 재시도

사용법 (pytimefolio 폴더, 가상환경 활성화):
    python preopen_order.py --at now        # 연습: 지금 바로 인증·연결·조회만 하고, 보낼 주문 요청을 출력 (주문 안 보냄)
    nohup python preopen_order.py --live > preopen.out 2>&1 &     # 실제 주문
    tail -f preopen.out

주의: 노트북이 잠들면 스크립트도 멈춘다. 09:15까지 덮개를 닫지 말고 전원을 연결해 둘 것.
인증: jar2.txt(curl 쿠키 파일)의 refreshToken으로 SilentRefresh를 불러 새 토큰을 받는다. 비밀번호는 쓰지 않는다.
"""

import argparse
import asyncio
import datetime as dt
import json
import time
from pathlib import Path

import httpx
import websockets

BASE = "https://contest.timefolio.net"
RS = "\x1e"
KST = dt.timezone(dt.timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
RESPONSES_DIR = ROOT / "data" / "responses"
COOKIES_DIR = ROOT / "data" / "cookies"
RESPONSES_DIR.mkdir(parents=True, exist_ok=True)


def now() -> dt.datetime:
    return dt.datetime.now(KST)


def log(msg: str):
    print(f"[{now():%H:%M:%S}] {msg}", flush=True)


def cookie_header(jar_path: str) -> str:
    """curl 쿠키 파일(Netscape 형식)에서 API 경로(/)에 붙는 쿠키를 모두 모아 Cookie 헤더로 만든다.
    curl -b와 같은 동작: refreshToken만 보내면 서버가 401을 돌려줄 수 있다(userid 등이 함께 필요)."""
    pairs = []
    for line in open(jar_path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) == 7 and f[2] == "/":
            pairs.append(f"{f[5]}={f[6]}")
    if not any(p.startswith("refreshToken=") for p in pairs):
        raise SystemExit(f"{jar_path}에 refreshToken이 없음")
    return "; ".join(pairs)


async def silent_refresh(http: httpx.AsyncClient, jar_path: str) -> dict:
    r = await http.post(f"{BASE}/api/Auth/SilentRefresh",
                        headers={"Cookie": cookie_header(jar_path)})
    r.raise_for_status()
    d = r.json()
    return {"token": d["accessToken"], "biz": d["currBizDay"]}


class Hub:
    def __init__(self, token, biz, pf):
        self.token, self.biz, self.pf, self.ws, self.last_t = token, biz, pf, None, 0

    async def connect(self, http):
        r = await http.post(f"{BASE}/hubs/main/negotiate", params={"negotiateVersion": 1},
                            headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        self.ws = await websockets.connect(
            f"wss://contest.timefolio.net/hubs/main?id={r.json()['connectionToken']}",
            additional_headers={"Authorization": f"Bearer {self.token}"}, max_size=2**20)
        await self.ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        await self.ws.recv()
        self.last_t = 0

    async def poll(self) -> dict:
        req = {"type": 1, "target": "RequestPortfolio",
               "arguments": [{"intv": 3, "auto": True, "lastT": self.last_t, "d": self.biz, "pfId": self.pf}]}
        await self.ws.send(json.dumps(req) + RS)
        while True:
            frame = await asyncio.wait_for(self.ws.recv(), timeout=20)
            for raw in filter(None, frame.split(RS)):
                m = json.loads(raw)
                if m.get("type") == 6:
                    await self.ws.send(json.dumps({"type": 6}) + RS)
                elif m.get("type") == 7:
                    raise websockets.ConnectionClosedOK(None, None)
                elif m.get("type") == 1 and m.get("target") == "ReceivePortfolio":
                    p = m["arguments"][0]
                    self.last_t = p.get("t", self.last_t)
                    return p


class State:
    """전체(FULL)는 교체, 증분(DIFF)은 적용. updated의 형식을 아직 모르므로 원문을 diff 로그에 남긴다."""

    def __init__(self):
        self.pos, self.ords = {}, {}

    def update(self, p: dict, difflog):
        if p.get("pos") is not None:
            self.pos = {r["Id"]: r for r in p["pos"]}
        if p.get("ords") is not None:
            self.ords = {r["Id"]: r for r in p["ords"]}
        for key, store in (("pos_diff", self.pos), ("ords_diff", self.ords)):
            d = p.get(key)
            if not d or not any(d.get(k) for k in ("added", "removed", "updated", "ords_chgd")):
                continue
            difflog.write(json.dumps({"kst": now().isoformat(), "key": key, "diff": d}, ensure_ascii=False) + "\n")
            difflog.flush()
            for r in d.get("added") or []:
                if isinstance(r, dict) and "Id" in r:
                    store[r["Id"]] = r
            for i in d.get("removed") or []:
                store.pop(i if not isinstance(i, dict) else i.get("Id"), None)
            for i, ch in (d.get("updated") or {}).items():
                key_id = int(i) if str(i).isdigit() else i
                if isinstance(ch, dict):
                    store[key_id] = {**store.get(key_id, {}), **ch}


def brief_order(o: dict) -> str:
    return (f"Id={o.get('Id')} state={o.get('state')} ls={o.get('ls')} ex={o.get('ex')} wei={o.get('wei')} "
            f"qty2ord={o.get('qty2ord')} cumQty={o.get('cumQty')} avgPx={o.get('avgPx')} "
            f"hm0={o.get('hm0')} hm1={o.get('hm1')} errMsg={o.get('errMsg')}")


def brief_pos(r: dict | None) -> str:
    if not r:
        return "보유 없음"
    return f"pos={r.get('pos')} wei={r.get('wei')} bQty={r.get('bQty')} bPrc={r.get('bPrc')} avgPrc={r.get('avgPrc')}"


async def main(a):
    at = now() if a.at == "now" else now().replace(hour=a.at_h, minute=a.at_m, second=0, microsecond=0)
    end = now().replace(hour=a.end_h, minute=a.end_m, second=0, microsecond=0)
    payload_preview = {"prodId": a.prod, "ls": "L", "ex": "E", "wei": a.wei, "hm0": a.hm0, "hm1": a.hm1}
    log(f"{'실제 주문' if a.live else '연습(주문 안 보냄)'} | {at:%H:%M}에 실행 | {payload_preview} | 기록 종료 {end:%H:%M}")
    if now() < at:
        log(f"{(at - now()).seconds // 60}분 {(at - now()).seconds % 60}초 대기")
        await asyncio.sleep((at - now()).total_seconds())

    async with httpx.AsyncClient(http2=False, timeout=15) as http:
        s = await silent_refresh(http, a.jar)
        today = now().strftime("%Y-%m-%d")
        log(f"SilentRefresh 200 | currBizDay={s['biz']} (오늘 {today})")
        if s["biz"] != today:
            log("서버 영업일이 오늘과 달라 중단")
            return

        hub = Hub(s["token"], s["biz"], a.pf)
        await hub.connect(http)
        st = State()
        difflog = open(RESPONSES_DIR / "preopen_diffs.jsonl", "a", encoding="utf-8")
        st.update(await hub.poll(), difflog)
        before = next((r for r in st.pos.values() if r.get("prodId") == a.prod), None)
        log(f"주문 전 {a.prod}: {brief_pos(before)} | 미체결 포함 주문 {len(st.ords)}건")

        body = {"d": s["biz"], "pfId": a.pf, "prodId": a.prod, "ls": "L", "ex": "E", "wei": a.wei,
                "exitAll": False, "hm0": a.hm0, "hm1": a.hm1, "limitIdx": 5, "limitPrc": None, "stopPrc": None}
        if not a.live:
            log(f"연습 모드: 보낼 요청 = POST /api/Portfolio/AddOrder {json.dumps(body, ensure_ascii=False)}")
            return
        attempts = []

        async def place(b: dict) -> int | None:
            """AddOrder 1회. 응답 코드(타임아웃·연결 오류면 None)를 돌려주고 모든 시도를 파일에 남긴다."""
            try:
                r = await http.post(f"{BASE}/api/Portfolio/AddOrder", json=b,
                                    headers={"Authorization": f"Bearer {s['token']}"})
                code, text = r.status_code, r.text
            except httpx.HTTPError as e:
                code, text = None, f"{type(e).__name__}: {e}"
            log(f"AddOrder → {code} | 응답 {text[:300]!r}")
            attempts.append({"kst": now().isoformat(), "request": b, "status": code, "response": text})
            json.dump(attempts, open(RESPONSES_DIR / "preopen_addorder.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            return code

        code = await place(body)
        if code == 401:                                   # 토큰 문제: 갱신 후 즉시 1회
            s.update(await silent_refresh(http, a.jar))
            code = await place(body)
        # 재시도 정책: 400(형식 오류)은 다시 보내도 같으므로 하지 않는다.
        # 200(에러 주문 여부는 아래에서 확인), 5xx, 타임아웃은 09:00 이후 주문 목록을 확인하고 없으면 1회 재시도.
        retry_pending = code != 400
        # 주문이 실시간 목록에 반영될 시간을 주기 위해, 마지막 시도 후 최소 30초는 기다린 뒤 확인한다 (중복 주문 방지)
        retry_at = max(now().replace(hour=9, minute=0, second=20, microsecond=0), now() + dt.timedelta(seconds=30))
        if code == 400:
            log("400: 요청 형식 오류로 보고 재시도하지 않음. 기록만 계속함")

        def live_order() -> dict | None:
            """오늘 이 종목의 주문 중 에러가 없는 것."""
            for o in st.ords.values():
                if o.get("prodId") == a.prod and o.get("d") == s["biz"] and not o.get("errMsg"):
                    return o
            return None

        seen_ord, seen_pos = {}, None
        token_t = time.time()
        while now() < end:
            try:
                if time.time() - token_t > 12 * 60:      # REST용 토큰 갱신 (WebSocket은 그대로 유지)
                    s.update(await silent_refresh(http, a.jar))
                    token_t = time.time()
                st.update(await hub.poll(), difflog)
            except (websockets.ConnectionClosed, asyncio.TimeoutError) as e:
                log(f"연결 문제({type(e).__name__}) → 토큰 갱신 후 재연결")
                s.update(await silent_refresh(http, a.jar))
                token_t = time.time()
                hub.token = s["token"]
                await hub.connect(http)
                continue
            for o in st.ords.values():
                if o.get("prodId") != a.prod:
                    continue
                b = brief_order(o)
                if seen_ord.get(o.get("Id")) != b:
                    log(f"주문 {b}")
                    seen_ord[o.get("Id")] = b
            if retry_pending and now() >= retry_at:
                retry_pending = False                     # 재시도는 최대 1회
                if live_order():
                    log("09:00 확인: 에러 없는 주문이 이미 있음 → 재시도하지 않음")
                else:
                    start = (now() + dt.timedelta(minutes=1)).strftime("%H:%M")
                    log(f"09:00 확인: 유효한 주문 없음 → 1회 재시도 (hm0={start})")
                    await place({**body, "hm0": start})
            p = brief_pos(next((r for r in st.pos.values() if r.get("prodId") == a.prod), None))
            if p != seen_pos:
                log(f"보유 {a.prod}: {p}")
                seen_pos = p
            await asyncio.sleep(3)
        log("기록 종료")
        json.dump({"ords": list(st.ords.values())}, open(RESPONSES_DIR / "preopen_final_ords.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="실제로 주문을 보냄. 없으면 연습 모드")
    ap.add_argument("--jar", default=str(COOKIES_DIR / "jar2.txt"))
    ap.add_argument("--pf", type=int, default=27032)
    ap.add_argument("--prod", default="A079550")          # LIG디펜스앤에어로스페이스
    ap.add_argument("--wei", type=float, default=0.3)     # 단위 %. 목표든 증분이든 소액
    ap.add_argument("--hm0", default="09:00")
    ap.add_argument("--hm1", default="09:10")
    ap.add_argument("--at", default="08:30", help="주문 시각 HH:MM 또는 now")
    ap.add_argument("--until", default="09:15", help="기록 종료 시각 HH:MM")
    a = ap.parse_args()
    a.at_h, a.at_m = (0, 0) if a.at == "now" else map(int, a.at.split(":"))
    a.end_h, a.end_m = map(int, a.until.split(":"))
    asyncio.run(main(a))
