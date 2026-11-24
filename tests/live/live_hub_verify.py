"""증분 병합 검증 (시점 차이 제거).
연결 A는 증분으로 상태를 쌓고, 연결 B는 매번 lastT=0 으로 스냅샷만 받는다.
둘을 동시에 요청해서 같은 t 를 받은 회차만 비교한다. 다른 값은 실제 값까지 출력한다. 주문 없음."""
import asyncio, logging
from pytimefolio.transport import Transport
from pytimefolio.auth import Auth
from pytimefolio.rest import Rest
from pytimefolio.hub import Hub

async def main(rounds=15):
    logging.basicConfig(level=logging.WARNING)
    async with Transport() as t:
        a = Auth(t); await a.start()
        pf = Rest(a).select_portfolio(91)
        A, B = Hub(a, pf), Hub(a, pf)
        await asyncio.gather(A.connect(), B.connect())
        same = bad = 0
        for i in range(rounds):
            B._last_t = 0                                    # B는 늘 스냅샷
            ka, kb = await asyncio.gather(A.poll_once(), B.poll_once())
            if A.state.t != B.state.t:
                print(f"#{i} {ka}/{kb} t 다름 ({A.state.t} vs {B.state.t}) -> 건너뜀"); await asyncio.sleep(3); continue
            same += 1
            diffs = []
            for f in ("pos", "ords"):
                sa, sb = getattr(A.state, f), getattr(B.state, f)
                for k in set(sa) | set(sb):
                    ra, rb = sa.get(k, {}), sb.get(k, {})
                    for x in set(ra) | set(rb):
                        if ra.get(x) != rb.get(x):
                            diffs.append(f"{f}[{k}].{x}: 증분={ra.get(x)!r} 스냅샷={rb.get(x)!r}")
            bad += bool(diffs)
            print(f"#{i} {ka}/{kb} t={A.state.t} 차이 {len(diffs)}개")
            for d in diffs[:5]: print("   ", d)
            await asyncio.sleep(3)
        print(f"같은 t 회차 {same}번 중 차이 있는 회차 {bad}번")
        await asyncio.gather(A.close(), B.close())

asyncio.run(main())
