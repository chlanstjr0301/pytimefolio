"""
검수 피드백 중 장 시작 전에 할 수 있는 실험 모음. 요청 수가 적다(실험당 0~5건).

사용법 (pytimefolio 폴더, 가상환경 활성화):
    pip install "httpx[http2]"          # h2 패키지 포함. 래퍼에도 필요함
    python probe_feedback.py tls        # TLS 버전·암호군·체인 길이·ALPN (요청 1건)
    python probe_feedback.py settings   # 서버 HTTP/2 SETTINGS (RFC 8441 지원 여부) (요청 1건)
    python probe_feedback.py dns        # 이름 조회 처음/두 번째 시간 (요청 0건)
    python probe_feedback.py idle       # 유휴 30/60/90/120초 뒤 연결 재사용 여부 (요청 5건, 약 5분)
    python probe_feedback.py push       # auto:true / auto:false로 1회 요청 후 60초 침묵 (요청 2건, 약 2분)
    nohup python probe_feedback.py tolerance > tolerance.out 2>&1 &   # 허용 오차 경계 (요청 4건, 약 21분)

인증이 필요한 실험(idle, push, tolerance)은 jar2.txt의 refreshToken으로 SilentRefresh만 부른다.
로그아웃처럼 쿠키를 무효로 만들 수 있는 요청은 보내지 않는다.
"""

import os
import asyncio
import datetime as dt
import json
import socket
import ssl
import sys
import time

HOST = "contest.timefolio.net"
BASE = f"https://{HOST}"
KST = dt.timezone(dt.timedelta(hours=9))
RS = "\x1e"


def now() -> str:
    return dt.datetime.now(KST).strftime("%H:%M:%S.%f")[:-3]


def cookie_header(jar_path: str = "data/cookies/jar2.txt") -> str:
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


def silent_refresh_sync() -> dict:
    import httpx
    r = httpx.post(f"{BASE}/api/Auth/SilentRefresh", headers={"Cookie": cookie_header()}, timeout=15)
    r.raise_for_status()
    d = r.json()
    return {"token": d["accessToken"], "biz": d["currBizDay"]}


# ------------------------------------------------------------------ tls
def cmd_tls():
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    t0 = time.perf_counter()
    with socket.create_connection((HOST, 443), timeout=10) as raw:
        t1 = time.perf_counter()
        with ctx.wrap_socket(raw, server_hostname=HOST) as s:
            t2 = time.perf_counter()
            chain = s.get_verified_chain() if hasattr(s, "get_verified_chain") else None
            cert = s.getpeercert()
            print(f"TLS 버전      : {s.version()}")
            print(f"암호군        : {s.cipher()[0]}")
            print(f"ALPN 선택     : {s.selected_alpn_protocol()}")
            print(f"인증서 주체   : {dict(x[0] for x in cert['subject']).get('commonName')}")
            print(f"발급자        : {dict(x[0] for x in cert['issuer']).get('organizationName')}")
            print(f"검증된 체인   : {len(chain) if chain else '확인 불가(Python 3.13 미만)'}개 인증서")
            print(f"TCP 연결 {1000*(t1-t0):.1f}ms / TLS 핸드셰이크 {1000*(t2-t1):.1f}ms (같은 프로세스, DNS 제외)")


# ------------------------------------------------------------------ settings
def cmd_settings():
    import h2.config
    import h2.connection
    import h2.events
    import h2.settings
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2"])
    with socket.create_connection((HOST, 443), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname=HOST) as s:
        assert s.selected_alpn_protocol() == "h2", "h2 협상 실패"
        c = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=True))
        c.initiate_connection()
        s.sendall(c.data_to_send())
        got = None
        s.settimeout(5)
        while got is None:
            data = s.recv(65535)
            if not data:
                break
            for ev in c.receive_data(data):
                if isinstance(ev, h2.events.RemoteSettingsChanged):
                    got = {h2.settings.SettingCodes(k).name if k in h2.settings.SettingCodes._value2member_map_ else k: v.new_value
                           for k, v in ev.changed_settings.items()}
            s.sendall(c.data_to_send())
        print("서버 SETTINGS:", json.dumps(got, indent=1))
        print("ENABLE_CONNECT_PROTOCOL(0x8, RFC 8441):",
              got.get("ENABLE_CONNECT_PROTOCOL", got.get(8, "보내지 않음 → HTTP/2 위의 WebSocket 미지원")) if got else "?")
        c.close_connection()
        s.sendall(c.data_to_send())


# ------------------------------------------------------------------ dns
def cmd_dns():
    for i in (1, 2, 3):
        t0 = time.perf_counter()
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)})
        print(f"getaddrinfo #{i}: {1000*(time.perf_counter()-t0):.1f}ms → {addrs}")
    print("TTL과 HTTPS 레코드는 셸에서: dig +noall +answer contest.timefolio.net A ; dig +short contest.timefolio.net HTTPS")


# ------------------------------------------------------------------ idle
async def cmd_idle():
    import httpx
    s = silent_refresh_sync()
    events = []

    async def trace(name, info):
        if name.startswith("connection.connect_tcp.started") or name.startswith("connection.start_tls.complete"):
            events.append(name)

    url = f"{BASE}/api/Portfolio/Session"
    async with httpx.AsyncClient(http2=True, headers={"Authorization": f"Bearer {s['token']}"}, timeout=15,
                                 limits=httpx.Limits(keepalive_expiry=600)) as http:
        async def one(label):
            events.clear()
            t0 = time.perf_counter()
            r = await http.get(url, extensions={"trace": trace})
            ms = 1000 * (time.perf_counter() - t0)
            new = any(e.startswith("connection.connect_tcp") for e in events)
            print(f"[{now()}] {label:<12} {r.status_code} {r.http_version} {ms:6.1f}ms  새 연결={'예' if new else '아니오'}", flush=True)
        await one("최초")
        for idle in (5, 15, 30, 60, 90):
            await asyncio.sleep(idle)
            await one(f"{idle}초 유휴 후")


# ------------------------------------------------------------------ push
async def cmd_push():
    import httpx
    import websockets
    s = silent_refresh_sync()
    auth = {"Authorization": f"Bearer {s['token']}"}
    async with httpx.AsyncClient() as http:
        for auto in (True, False):
            r = await http.post(f"{BASE}/hubs/main/negotiate", params={"negotiateVersion": 1}, headers=auth)
            r.raise_for_status()
            async with websockets.connect(f"wss://{HOST}/hubs/main?id={r.json()['connectionToken']}",
                                          additional_headers=auth, max_size=2**20) as ws:
                await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
                await ws.recv()
                req = {"type": 1, "target": "RequestPortfolio",
                       "arguments": [{"intv": 3, "auto": auto, "lastT": 0, "d": s["biz"], "pfId": int(os.environ["TF_PF_ID"])}]}
                await ws.send(json.dumps(req) + RS)
                print(f"[{now()}] auto={auto} 요청 1회 전송. 이후 60초 동안 요청 없이 ping만 보냄", flush=True)
                end, last_ping, n = time.time() + 60, time.time(), 0
                while time.time() < end:
                    if time.time() - last_ping >= 15:              # 서버 ClientTimeoutInterval(기본 30초) 대비
                        await ws.send(json.dumps({"type": 6}) + RS)
                        last_ping = time.time()
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    for raw in filter(None, frame.split(RS)):
                        m = json.loads(raw)
                        if m.get("type") == 1:
                            n += 1
                            p = m["arguments"][0]
                            kind = "FULL" if p.get("pos") is not None else "DIFF"
                            print(f"[{now()}]   ReceivePortfolio #{n} {kind} {len(raw)}B", flush=True)
                        elif m.get("type") == 7:
                            print(f"[{now()}]   close: {m}", flush=True)
                print(f"[{now()}] auto={auto}: 60초 동안 수신 {n}건 → {'서버가 스스로 보냄(푸시)' if n > 1 else '요청한 1건만 옴(푸시 없음)'}", flush=True)


# ------------------------------------------------------------------ tolerance
async def cmd_tolerance():
    import base64
    import httpx
    s = silent_refresh_sync()
    p = s["token"].split(".")[1]
    p += "=" * (-len(p) % 4)
    claims = json.loads(base64.urlsafe_b64decode(p))
    exp = claims["exp"]
    print(f"[{now()}] 토큰 발급, exp={dt.datetime.fromtimestamp(exp, KST):%H:%M:%S} (iat 기준 수명 {exp-claims['iat']}s)", flush=True)
    url = f"{BASE}/api/Portfolio/Session"
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {s['token']}"}, timeout=15) as http:
        r = await http.get(url)
        print(f"[{now()}] 기준 요청 {r.status_code} | 서버 Date: {r.headers.get('date')}", flush=True)
        for off in (290, 300, 310):                     # 만료 후 4:50, 5:00, 5:10
            wait = exp + off - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            r = await http.get(url)
            print(f"[{now()}] exp+{off//60}:{off%60:02d} → {r.status_code} | 서버 Date: {r.headers.get('date')} "
                  f"| {r.headers.get('www-authenticate', '')[:90]}", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"tls": cmd_tls, "settings": cmd_settings, "dns": cmd_dns, "idle": cmd_idle, "push": cmd_push,
          "tolerance": cmd_tolerance}.get(cmd)
    if fn is None:
        raise SystemExit(__doc__)
    r = fn()
    if asyncio.iscoroutine(r):
        asyncio.run(r)
