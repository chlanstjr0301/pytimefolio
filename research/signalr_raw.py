"""
SignalR 허브를 라이브러리 없이 직접 붙어 보는 실험 스크립트.

사용법 (WSL, refresh.json이 있는 폴더에서):
    pip install httpx websockets
    # 토큰이 15분 지났으면 먼저 SilentRefresh curl을 다시 실행해 refresh.json을 갱신
    python3 signalr_raw.py            # 기본 5회 요청
    python3 signalr_raw.py --n 10 --echo-lastT

프로토콜 요약 (SignalR JSON Hub Protocol v1)
    1) POST /hubs/main/negotiate?negotiateVersion=1  -> connectionToken
    2) WebSocket 연결: wss://.../hubs/main?id=<connectionToken>
    3) 핸드셰이크 전송: {"protocol":"json","version":1}<0x1E>   수신: {}<0x1E>
    4) 이후 모든 메시지는 JSON 하나 + 0x1E(Record Separator)로 끝남
       type 1 = Invocation (상대 쪽 메서드 호출)
       type 6 = Ping (연결 유지)
       type 7 = Close
"""

import argparse
import asyncio
import json
import time

import httpx
import websockets

BASE = "https://contest.timefolio.net"
RS = "\x1e"  # 메시지 구분자 0x1E. 문서 캡처에서 보이던 '␞'가 이것


def load_session():
    d = json.load(open("refresh.json"))
    return d["accessToken"], d["currBizDay"]


def describe(payload: dict) -> str:
    """ReceivePortfolio 인자의 구조만 요약 (값은 t, kstNow만)."""
    shape = {}
    for k, v in payload.items():
        if isinstance(v, list):
            shape[k] = f"list[{len(v)}]"
        elif isinstance(v, dict):
            shape[k] = f"dict{{{len(v)}}}"
        elif v is None:
            shape[k] = "None"
        else:
            shape[k] = type(v).__name__
    return f"t={payload.get('t')} kstNow={payload.get('kstNow')} keys={shape}"


async def main(pf_id: int, n: int, echo_lastT: bool, intv: float,
               lastT_now: bool = False, dump: bool = False):
    token, biz_day = load_session()
    auth = {"Authorization": f"Bearer {token}"}

    # ---- 1) negotiate: 그냥 HTTP POST ---------------------------------
    async with httpx.AsyncClient(http2=False) as http:
        r = await http.post(f"{BASE}/hubs/main/negotiate",
                            params={"negotiateVersion": 1}, headers=auth)
        r.raise_for_status()
        conn_token = r.json()["connectionToken"]
    print(f"[negotiate] 200, connectionToken {len(conn_token)}자")

    # ---- 2) WebSocket 연결 ---------------------------------------------
    # 브라우저 JS는 WebSocket에 헤더를 못 붙여서 토큰을 ?access_token= 으로 보낸다.
    # Python은 헤더를 붙일 수 있으므로 Authorization 헤더를 쓴다. (URL에 토큰이 안 남음)
    url = f"wss://contest.timefolio.net/hubs/main?id={conn_token}"
    async with websockets.connect(url, additional_headers=auth,
                                  max_size=8 * 2**20) as ws:
        # ---- 3) 핸드셰이크 ---------------------------------------------
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        hs = await ws.recv()
        print(f"[handshake] 수신 {hs!r}")  # 정상이면 '{}\x1e'

        last_t = int(time.time()) if lastT_now else 0
        for i in range(n):
            # ---- 4) RequestPortfolio 호출 (invocationId 없음 = 응답을 기다리지 않는 호출)
            req = {"type": 1, "target": "RequestPortfolio",
                   "arguments": [{"intv": 3, "auto": True, "lastT": last_t,
                                  "d": biz_day, "pfId": pf_id}]}
            sent_at = time.perf_counter()
            await ws.send(json.dumps(req) + RS)

            # 서버가 ReceivePortfolio를 호출해 줄 때까지 프레임을 읽는다.
            got = False
            while not got:
                frame = await asyncio.wait_for(ws.recv(), timeout=15)
                for raw in filter(None, frame.split(RS)):
                    msg = json.loads(raw)
                    ty = msg.get("type")
                    if ty == 6:
                        print("   [ping] 서버 → 클라이언트")
                        await ws.send(json.dumps({"type": 6}) + RS)
                    elif ty == 7:
                        print(f"   [close] {msg}")
                        return
                    elif ty == 1 and msg.get("target") == "ReceivePortfolio":
                        ms = (time.perf_counter() - sent_at) * 1000
                        p = msg["arguments"][0]
                        print(f"#{i+1} lastT={last_t:<10} 응답 {len(raw):>6}B "
                              f"{ms:6.1f}ms | {describe(p)}")
                        if dump:
                            kind = "full" if p.get("bs") is not None else "diff"
                            fn = f"rp_{i+1}_{kind}.json"
                            json.dump(p, open(fn, "w"), ensure_ascii=False, indent=1)
                            print(f"   → {fn} 저장")
                        if echo_lastT:
                            last_t = p.get("t", last_t)
                        got = True
                    else:
                        print(f"   [기타] type={ty} target={msg.get('target')}")
            await asyncio.sleep(intv)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pf", type=int, required=True, help="your portfolio ID (pfId)")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--echo-lastT", action="store_true",
                    help="직전 응답의 t를 다음 lastT로 보냄 (브라우저와 같은 방식)")
    ap.add_argument("--intv", type=float, default=3.0)
    ap.add_argument("--lastT-now", action="store_true",
                    help="첫 요청의 lastT를 클라이언트 시계(time.time())로 보냄 (기존 repo 방식 재현)")
    ap.add_argument("--dump", action="store_true",
                    help="각 응답을 rp_<번호>_<full|diff>.json 으로 저장")
    a = ap.parse_args()
    asyncio.run(main(a.pf, a.n, a.echo_lastT, a.intv, a.lastT_now, a.dump))
