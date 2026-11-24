"""
lastT 판정 기준 실험.

질문: 서버는 어떤 lastT에 증분(diff)을 주고, 어떤 lastT에 전체(full)를 주는가?
  (a) 이 연결에서 직전에 보낸 t와 정확히 같을 때만 diff
  (b) 서버가 기억하는 과거 시점이면(일정 범위 안) 그 시점부터의 diff
  (c) 연결과 무관하게 계정(pfId) 단위로 기억

사용법 (refresh.json 또는 r6.json이 있는 폴더, 가상환경 활성화 상태):
    python lastT_probe.py                  # 토큰은 refresh.json에서
    python lastT_probe.py --session r6.json
"""

import argparse
import asyncio
import json

import httpx
import websockets

BASE = "https://contest.timefolio.net"
RS = "\x1e"


class Hub:
    """SignalR 허브 연결 하나. RequestPortfolio를 보내고 ReceivePortfolio를 받는다."""

    def __init__(self, token: str, biz_day: str, pf_id: int):
        self.auth = {"Authorization": f"Bearer {token}"}
        self.biz_day, self.pf_id = biz_day, pf_id
        self.ws = None

    async def __aenter__(self):
        async with httpx.AsyncClient() as http:
            r = await http.post(f"{BASE}/hubs/main/negotiate",
                                params={"negotiateVersion": 1}, headers=self.auth)
            r.raise_for_status()
            tok = r.json()["connectionToken"]
        self.ws = await websockets.connect(
            f"wss://contest.timefolio.net/hubs/main?id={tok}",
            additional_headers=self.auth, max_size=8 * 2**20)
        await self.ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        assert (await self.ws.recv()) == "{}" + RS, "handshake 실패"
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def request(self, last_t: int) -> tuple[str, int, dict]:
        req = {"type": 1, "target": "RequestPortfolio",
               "arguments": [{"intv": 3, "auto": True, "lastT": last_t,
                              "d": self.biz_day, "pfId": self.pf_id}]}
        await self.ws.send(json.dumps(req) + RS)
        while True:
            frame = await asyncio.wait_for(self.ws.recv(), timeout=15)
            for raw in filter(None, frame.split(RS)):
                msg = json.loads(raw)
                if msg.get("type") == 6:
                    await self.ws.send(json.dumps({"type": 6}) + RS)
                elif msg.get("type") == 1 and msg.get("target") == "ReceivePortfolio":
                    p = msg["arguments"][0]
                    kind = ("FULL" if p.get("pos") is not None
                            else "DIFF" if p.get("pos_diff") is not None else "??")
                    return kind, len(raw), p


async def step(hub: Hub, label: str, last_t: int, hist: list[int]):
    kind, size, p = await hub.request(last_t)
    hist.append(p["t"])
    print(f"  {label:<34} lastT={last_t:<11} → {kind:4} {size:>5}B  t={p['t']}")
    await asyncio.sleep(3)  # 브라우저와 같은 간격. 서버 부담을 늘리지 않기 위함
    return kind


async def main(session_file: str, pf_id: int):
    s = json.load(open(session_file))
    token, biz_day = s["accessToken"], s["currBizDay"]

    print("[연결 A]")
    async with Hub(token, biz_day, pf_id) as a:
        h: list[int] = []
        await step(a, "1. lastT=0", 0, h)                       # 기준: FULL 예상
        await step(a, "2. 직전 t", h[-1], h)                     # 기준: DIFF 예상
        await step(a, "3. 하나 앞의 t (6초 전, 서버가 보낸 값)", h[-2], h)
        await step(a, "4. 직전 t − 1 (서버가 보낸 적 없는 값)", h[-1] - 1, h)
        await step(a, "5. 직전 t − 60 (1분 전)", h[-1] - 60, h)
        await step(a, "6. 직전 t − 3600 (1시간 전)", h[-1] - 3600, h)
        await step(a, "7. 직전 t + 100 (미래)", h[-1] + 100, h)
        await step(a, "8. 같은 lastT 두 번 반복 (직전 t)", h[-1], h)
        await step(a, "   └ 같은 값 다시", h[-2], h)
        carry = h[-1]

    print("[연결 B: 새 연결]")
    async with Hub(token, biz_day, pf_id) as b:
        h2: list[int] = []
        await step(b, "9. 연결 A의 마지막 t", carry, h2)
        await step(b, "10. 직전 t", h2[-1], h2)

    print("\n판정 기준")
    print("  3이 DIFF → 직전 값이 아니어도 받아 줌 (a 아님)")
    print("  4가 DIFF → 서버가 보낸 적 없는 값도 받아 줌 → 시각 기반 (b)")
    print("  5·6 결과 → 증분으로 받아 주는 시간 범위")
    print("  9가 DIFF → 연결이 아니라 계정·시각 기준으로 기억 (c)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="refresh.json")
    ap.add_argument("--pf", type=int, default=27032)
    a = ap.parse_args()
    asyncio.run(main(a.session, a.pf))
