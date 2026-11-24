"""
WebSocket 연결 중에 액세스 토큰이 만료되면 어떻게 되는가?

가설
  (A) 인증은 연결할 때 한 번만 확인 → 토큰이 만료돼도 연결과 응답이 계속 유지된다
  (B) 서버가 만료 시각(+허용 오차 5분)에 연결을 끊는다 (close 메시지 또는 WebSocket 종료)
  (C) 연결은 유지되지만 RequestPortfolio에 에러/빈 응답이 온다

사용법 (가상환경 활성화, 방금 SilentRefresh로 받은 세션 파일 지정):
    nohup python ws_expiry_probe.py --session r7.json > ws_expiry.out 2>&1 &
    tail -f ws_expiry.out          # 진행 상황 보기 (Ctrl+C는 tail만 끈다)

기본 28분 동안 3초 간격으로 요청한다. 토큰 exp + 8분이 지나면 스스로 끝난다.
연결이 끊기면, 같은(만료된) 토큰으로 negotiate를 다시 시도해 재연결에 새 토큰이 필요한지도 확인한다.
"""

import argparse
import asyncio
import base64
import datetime as dt
import json
import time

import httpx
import websockets

BASE = "https://contest.timefolio.net"
RS = "\x1e"
KST = dt.timezone(dt.timedelta(hours=9))


def now() -> str:
    return dt.datetime.now(KST).strftime("%H:%M:%S")


def jwt_exp(token: str) -> int:
    p = token.split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))["exp"]


def rel(exp: int) -> str:
    d = int(time.time()) - exp
    return f"exp{'+' if d >= 0 else '-'}{abs(d) // 60:02d}:{abs(d) % 60:02d}"


async def negotiate(token: str) -> tuple[int, str | None]:
    async with httpx.AsyncClient() as http:
        r = await http.post(f"{BASE}/hubs/main/negotiate", params={"negotiateVersion": 1},
                            headers={"Authorization": f"Bearer {token}"})
        return r.status_code, (r.json().get("connectionToken") if r.status_code == 200 else None)


async def main(session: str, pf_id: int, minutes: float):
    s = json.load(open(session))
    token, biz = s["accessToken"], s["currBizDay"]
    exp = jwt_exp(token)
    deadline = min(time.time() + minutes * 60, exp + 8 * 60)
    print(f"[{now()}] 시작 | 토큰 만료 {dt.datetime.fromtimestamp(exp, KST):%H:%M:%S} "
          f"| 종료 예정 {dt.datetime.fromtimestamp(deadline, KST):%H:%M:%S}", flush=True)

    code, ctok = await negotiate(token)
    if code != 200:
        print(f"[{now()}] negotiate 실패 {code}. 세션 파일의 토큰이 이미 만료됐을 수 있음", flush=True)
        return
    auth = {"Authorization": f"Bearer {token}"}
    ws = await websockets.connect(f"wss://contest.timefolio.net/hubs/main?id={ctok}",
                                  additional_headers=auth, max_size=2**20)
    await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
    print(f"[{now()}] handshake {await ws.recv()!r}", flush=True)

    last_t, n, last_kind, last_print = 0, 0, None, 0.0
    try:
        while time.time() < deadline:
            req = {"type": 1, "target": "RequestPortfolio",
                   "arguments": [{"intv": 3, "auto": True, "lastT": last_t, "d": biz, "pfId": pf_id}]}
            await ws.send(json.dumps(req) + RS)
            got = False
            while not got:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=20)
                except asyncio.TimeoutError:
                    print(f"[{now()}] {rel(exp)} 20초 동안 응답 없음 (연결은 열려 있음)", flush=True)
                    got = True
                    break
                for raw in filter(None, frame.split(RS)):
                    msg = json.loads(raw)
                    ty = msg.get("type")
                    if ty == 6:
                        await ws.send(json.dumps({"type": 6}) + RS)
                    elif ty == 7:
                        print(f"[{now()}] {rel(exp)} ★ 서버 close 메시지: {msg}", flush=True)
                        raise websockets.ConnectionClosedOK(None, None)
                    elif ty == 1 and msg.get("target") == "ReceivePortfolio":
                        n += 1
                        p = msg["arguments"][0]
                        kind = "FULL" if p.get("pos") is not None else "DIFF" if p.get("pos_diff") is not None else "??"
                        last_t = p.get("t", last_t)
                        # 형태가 바뀌거나 1분마다 한 줄씩만 출력
                        if kind != last_kind or time.time() - last_print >= 60:
                            print(f"[{now()}] {rel(exp)} #{n:<4} {kind} {len(raw):>5}B", flush=True)
                            last_kind, last_print = kind, time.time()
                        got = True
                    else:
                        print(f"[{now()}] {rel(exp)} 기타 메시지: {raw[:200]}", flush=True)
            await asyncio.sleep(3)
        print(f"[{now()}] {rel(exp)} 종료 시각 도달. 연결이 끝까지 유지됨 → 가설 (A)", flush=True)
    except websockets.ConnectionClosed as e:
        print(f"[{now()}] {rel(exp)} ★ 연결 종료: code={getattr(e, 'code', None)} "
              f"reason={getattr(e, 'reason', None)!r} (응답 {n}건 수신 후)", flush=True)
        c2, _ = await negotiate(token)
        print(f"[{now()}] {rel(exp)} 같은 토큰으로 재연결 negotiate → {c2}", flush=True)
    finally:
        await ws.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="r7.json")
    ap.add_argument("--pf", type=int, default=27032)
    ap.add_argument("--minutes", type=float, default=28)
    a = ap.parse_args()
    asyncio.run(main(a.session, a.pf, a.minutes))
