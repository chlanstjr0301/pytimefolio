"""auth 동작 확인. 첫 실행은 로그인(.env 사용), 두 번째부터는 저장된 쿠키로 갱신만 해야 한다.
토큰·쿠키·개인정보는 출력하지 않는다."""
import asyncio, logging, os
from pytimefolio.transport import Transport
from pytimefolio.auth import Auth, jwt_claims

def env(p=".env"):
    d = {}
    for line in open(p, encoding="utf-8"):
        line = line.strip().rstrip("\r")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); d[k.strip()] = v.strip().strip('"').strip("'")
    return d

async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    e = env()
    async with Transport() as t:
        a = Auth(t, e.get("EMAIL"), e.get("PW") or e.get("PASSWORD"))
        await a.start()
        c = jwt_claims(await a.token())
        print(f"영업일 {a.biz_day} | 토큰 수명 {c['exp']-c['iat']}s | 서버-PC {t.clock_offset:+.2f}s")
        r = await a.call("GET", "/api/Portfolio/Session", params={"d": a.biz_day})
        print("Session:", r.json())
        print("세션 파일:", a.session_file, oct(os.stat(a.session_file).st_mode & 0o777))

asyncio.run(main())
