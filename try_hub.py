"""hub + state 동작 확인 (주문 없음).
60 s 동안 폴링하며 증분을 누적하고, 마지막에 새 연결로 스냅샷을 받아 누적 상태와 비교한다."""
import asyncio, logging, time
from pytimefolio.transport import Transport
from pytimefolio.auth import Auth
from pytimefolio.rest import Rest
from pytimefolio.hub import Hub

async def main(secs=60):
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Transport() as t:
        a = Auth(t); await a.start()
        pf = Rest(a).select_portfolio(91)
        hub = Hub(a, pf)
        t0 = time.monotonic()
        async for kind, st in hub.stream():
            lig = st.by_prod().get("A079550", {})
            print(f"{kind:4} t={st.t} 보유 {len(st.pos)} 주문 {len(st.ords)} | 삼성 wei={st.weight('A005930')} | LIG close={lig.get('close')}")
            if time.monotonic() - t0 > secs:
                break
        await hub.close()
        # 검증: 새 연결의 스냅샷과 비교 (prft, chPct 처럼 매 순간 변하는 값은 시점 차이로 다를 수 있음)
        check = Hub(a, pf); await check.connect(); await check.poll_once()
        d = hub.state.snapshot_equal(check.state)
        print(f"스냅샷 {hub.state.snapshots}회, 증분 {hub.state.diffs}회 | 새 스냅샷과 다른 레코드 {len(d)}개")
        for line in d[:8]: print("  ", line)
        await check.close()

asyncio.run(main())
