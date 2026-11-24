"""transport 동작 확인: 인증 없이 되는 요청으로 HTTP 버전, 연결 재사용, 시계 오프셋을 본다."""
import asyncio, time
from pytimefolio.transport import Transport
from pytimefolio.errors import AuthError

async def main():
    async with Transport() as t:
        for i in range(5):
            t0 = time.perf_counter()
            try:
                r = await t.request("GET", "/api/Portfolio/Session", params={"d": "2026-11-24"})
                code = r.status_code; ver = r.http_version
            except AuthError:
                code, ver = 401, "?"
            print(f"#{i} {code} {ver} {1000*(time.perf_counter()-t0):6.1f} ms  offset={t.clock_offset:+.2f}s")
        print("서버 시계 - PC 시계 =", round(t.clock_offset, 2), "s")

asyncio.run(main())
