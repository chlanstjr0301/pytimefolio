"""Logout 실험: 버릴 세션(새 로그인)을 만들어 Logout이 무엇을 끊는지 확인한다.
주문 없음. jar2.txt는 읽기만 한다(갱신 가능 여부 확인용). 토큰·비밀번호는 출력하지 않는다."""
import asyncio, datetime as dt, json, os, sys
import httpx

BASE = "https://contest.timefolio.net"
KST = dt.timezone(dt.timedelta(hours=9))
def log(m): print(f"[{dt.datetime.now(KST):%H:%M:%S}] {m}", flush=True)

def load_env(p=".env"):
    env = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip().rstrip("\r")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return env

def jar_header(path):
    pairs = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("#HttpOnly_"): line = line[10:]
        elif not line or line.startswith("#"): continue
        f = line.split("\t")
        if len(f) == 7 and f[2] == "/": pairs.append(f"{f[5]}={f[6]}")
    return "; ".join(pairs)

def setcookie_summary(r):
    out = []
    for h in r.headers.get_list("set-cookie"):
        name = h.split("=", 1)[0]
        low = h.lower()
        val_empty = h.split("=", 1)[1].split(";", 1)[0] == ""
        exp = next((p.strip() for p in h.split(";") if p.strip().lower().startswith(("expires", "max-age"))), "")
        out.append(f"{name}(값 {'비움' if val_empty else '있음'}, {exp})")
    return ", ".join(out) or "없음"

async def refresh(http, cookie, label):
    r = await http.post(f"{BASE}/api/Auth/SilentRefresh", headers={"Cookie": cookie})
    log(f"{label}: SilentRefresh {r.status_code}")
    return r.json().get("accessToken") if r.status_code == 200 else None

async def main():
    env = load_env()
    email = env.get("EMAIL") or env.get("TF_EMAIL")
    pw = env.get("PW") or env.get("PASSWORD") or env.get("TF_PW")
    if not email or not pw: sys.exit(".env에서 EMAIL / PW를 못 찾음")
    jar2 = sys.argv[1] if len(sys.argv) > 1 else "data/cookies/jar2.txt"
    async with httpx.AsyncClient(http2=True, timeout=15) as http:
        # 1. 버릴 세션 만들기
        r = await http.post(f"{BASE}/api/Auth/Login", json={"email": email, "password": pw})
        log(f"1. 새 로그인 {r.status_code}")
        r.raise_for_status()
        biz = r.json()["currBizDay"]
        tok3 = r.json()["accessToken"]
        ck3 = "; ".join(f"{c.name}={c.value}" for c in http.cookies.jar if c.path == "/")
        log(f"   세션3 쿠키 {len([c for c in http.cookies.jar if c.path == '/'])}개 확보 (이름: {', '.join(c.name for c in http.cookies.jar if c.path == '/')})")
        http.cookies.clear()   # 이후 요청은 모두 Cookie 헤더를 직접 지정
        # 2. 로그아웃 전 갱신
        await refresh(http, ck3, "2. 세션3 로그아웃 전")
        # 3. 로그아웃
        r = await http.post(f"{BASE}/api/Auth/Logout", headers={"Cookie": ck3, "Authorization": f"Bearer {tok3}"})
        log(f"3. Logout {r.status_code} | 본문 {r.text[:120]!r} | Set-Cookie: {setcookie_summary(r)}")
        http.cookies.clear()
        await asyncio.sleep(2)
        # 4. 저장해 둔 쿠키로 다시 갱신
        t = await refresh(http, ck3, "4. 세션3 로그아웃 후 (저장한 쿠키)")
        log("   → " + ("서버가 세션을 끊지 않음 (쿠키 삭제만 요청)" if t else "서버가 refresh 쿠키를 무효화함"))
        # 5. 로그아웃 전 access token이 REST에서 아직 통하는지
        r = await http.get(f"{BASE}/api/Portfolio/Session", params={"d": biz}, headers={"Authorization": f"Bearer {tok3}"})
        log(f"5. 로그아웃 전에 받은 access token으로 REST {r.status_code} (200이면 JWT는 만료까지 유효)")
        # 6. 다른 세션
        await refresh(http, jar_header(jar2), f"6. 다른 세션({os.path.basename(jar2)})")
        if os.path.exists("data/cookies/jar.txt"):
            await refresh(http, jar_header("data/cookies/jar.txt"), "6. 다른 세션(jar.txt)")

asyncio.run(main())
