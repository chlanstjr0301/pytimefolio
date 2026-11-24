"""주문 없이 확인: 증분의 bs_updated 내용, 주문 기록의 wei 자릿수, 피드 신선도.
60 s 동안 피드를 받으면서 bs_updated 가 dict 로 오는지, 어떤 키가 바뀌는지 본다."""
import asyncio, json, logging
from pytimefolio import Timefolio

async def main(secs=60):
    logging.basicConfig(level=logging.WARNING)
    async with Timefolio(ctst_id=91) as tf:
        st = tf.state
        na0 = (st.bs or {}).get("na")
        print("스냅샷 bs_updated:", json.dumps(st.extra.get("bs_updated"), ensure_ascii=False)[:300])
        seen = {}
        for _ in range(secs // 3):
            await asyncio.sleep(3)
            v = st.extra.get("bs_updated")
            seen[type(v).__name__] = seen.get(type(v).__name__, 0) + 1
        print("증분 동안 bs_updated 타입별 횟수:", seen, "| 마지막 값:", json.dumps(st.extra.get("bs_updated"), ensure_ascii=False)[:300])
        print(f"bs.na 처음 {na0:,} -> 지금 {(st.bs or {}).get('na'):,} (바뀌었으면 병합이 동작)")
        ws = sorted({str(o.get('wei')) for o in st.ords.values()})
        print("주문 기록 wei 값들:", ws)
        print(f"피드 마지막 응답 {tf.hub.age():.1f} s 전, 연속 실패 {tf.hub.failures}회")

asyncio.run(main())
