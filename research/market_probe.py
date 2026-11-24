"""장중 실험: push(Q6), delta(Q5), race(Q7). 주문은 보내지 않는다."""
import argparse
import asyncio
import datetime as dt
import json
import time

import httpx
import websockets

BASE = "https://contest.timefolio.net"
RS = "\x1e"
KST = dt.timezone(dt.timedelta(hours=9))


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
                    args = m.get("arguments") or [None]
                    p = args[0]
                    if not isinstance(p, dict):          # 서버가 null을 보내는 경우가 있음 (2026-11-24 08:30 관찰)
                        log(f"ReceivePortfolio 내용이 비어 있음: {raw[:200]!r}")
                        self.last_t = 0                  # 다음 요청은 전체 스냅샷을 받도록
                        return {}
                    self.last_t = p.get("t", self.last_t)
                    return p



PF = 27032


async def push_test(http, a):
    """Q6: 요청 1번만 보내고 60초 동안 아무것도 안 보낸 채 서버가 먼저 보내는 게 있는지 센다."""
    s = await silent_refresh(http, a.jar)
    hub = Hub(s["token"], s["biz"], PF)
    await hub.connect(http)
    p = await hub.poll()
    log(f"첫 응답: {'스냅샷' if p.get('pos') is not None else '증분/빈값'} t={p.get('t')}")
    t0, n = time.monotonic(), 0
    while time.monotonic() - t0 < a.secs:
        try:
            frame = await asyncio.wait_for(hub.ws.recv(), timeout=a.secs - (time.monotonic() - t0))
        except asyncio.TimeoutError:
            break
        for raw in filter(None, frame.split(RS)):
            m = json.loads(raw)
            if m.get("type") == 6:
                await hub.ws.send(json.dumps({"type": 6}) + RS)
                log("ping(type 6) 받음 -> 응답")
                continue
            n += 1
            log(f"요청 없이 받은 메시지 #{n}: type={m.get('type')} target={m.get('target')} {len(raw)}B")
    log(f"결과: {a.secs}s 동안 요청 없이 받은 메시지 {n}개 (ping 제외)")
    await hub.ws.close()


async def delta_test(http, a):
    """Q5: 3초 폴링을 a.secs 동안 하며 응답 크기와 diff 내용을 기록한다. updated의 원문을 jsonl로 저장."""
    s = await silent_refresh(http, a.jar)
    hub = Hub(s["token"], s["biz"], PF)
    await hub.connect(http)
    out = open(a.out, "w", encoding="utf-8")
    t0 = time.monotonic()
    while time.monotonic() - t0 < a.secs:
        req_t = hub.last_t
        p = await hub.poll()
        size = len(json.dumps(p, ensure_ascii=False))
        kind = "FULL" if p.get("pos") is not None else ("NULL" if not p else "DIFF")
        counts = {}
        for k in ("pos_diff", "ords_diff", "msecs_diff"):
            d = p.get(k) or {}
            counts[k] = {f: len(d.get(f) or []) for f in ("added", "removed", "updated", "ords_chgd") if d.get(f)}
        log(f"{kind} {size}B lastT={req_t} t={p.get('t')} {json.dumps({k: v for k, v in counts.items() if v})}")
        out.write(json.dumps({"kst": now().isoformat(), "kind": kind, "size": size, "reply": p}, ensure_ascii=False) + "\n")
        out.flush()
        await asyncio.sleep(3)
    await hub.ws.close()
    log(f"저장: {a.out}")


async def refresh_race(http, a):
    """Q7: 같은 쿠키로 SilentRefresh 두 번을 동시에 보낸다. 다른 jar를 쓴다(jar2는 모니터가 사용 중)."""
    ck = cookie_header(a.jar)
    async def one(i):
        r = await http.post(f"{BASE}/api/Auth/SilentRefresh", headers={"Cookie": ck})
        sc = [h for h in r.headers.get_list("set-cookie") if h.startswith("refreshToken=")]
        log(f"#{i} {r.status_code} 새 refreshToken 쿠키={'있음' if sc else '없음'}")
        return r.status_code
    res = await asyncio.gather(one(1), one(2))
    r3 = await http.post(f"{BASE}/api/Auth/SilentRefresh", headers={"Cookie": ck})
    log(f"동시 2건 {res}, 직후 1건 더 {r3.status_code} (200이면 동시 갱신도 쿠키를 망가뜨리지 않음)")


async def main(a):
    async with httpx.AsyncClient(http2=True, timeout=15) as http:
        await {"push": push_test, "delta": delta_test, "race": refresh_race}[a.mode](http, a)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["push", "delta", "race"])
    ap.add_argument("--jar", default="data/cookies/jar2.txt")
    ap.add_argument("--secs", type=int, default=60)
    ap.add_argument("--out", default="data/responses/market_deltas.jsonl")
    asyncio.run(main(ap.parse_args()))
