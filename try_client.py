"""Timefolio 클라이언트 확인 (주문 없음): 시작, 비중, 남은 한도, rebalance 계획(dry run)."""
import asyncio, logging
from pytimefolio import Timefolio

async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Timefolio(ctst_id=91) as tf:
        w = tf.weights()
        print("보유", len(w), "종목, 합계", round(sum(w.values()), 2), "% | 남은 주문 한도", tf.orders_left())
        print("상위 5:", sorted(w.items(), key=lambda x: -x[1])[:5])
        tg = dict(w); tg["A005930"] = 50.0                     # 일부러 한도(삼성 40%)를 넘겨 본다
        for o in tf.plan(tg, limit_idx=5, hm0="10:00", hm1="10:30"):
            print("  계획:", o.prod_id, round(o.wei, 2), "매도" if o.exit else "매수")
        await asyncio.sleep(6)
        print("6 s 뒤 피드 갱신 횟수: 스냅샷", tf.state.snapshots, "증분", tf.state.diffs)

asyncio.run(main())
