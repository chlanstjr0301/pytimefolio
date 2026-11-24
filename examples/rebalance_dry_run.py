"""섹터 한도 검사 확인 (주문 없음)."""
import asyncio, logging
from pytimefolio import Timefolio

async def main():
    logging.basicConfig(level=logging.WARNING)
    async with Timefolio(ctst_id=91) as tf:
        w = tf.weights()
        ok = tf.plan(w, limit_idx=5)
        print("현재 비중 그대로:", len(ok), "건 (0건이어야 함)")
        tg = dict(w); tg["A329180"] = w["A329180"] + 6.0; tg["A005930"] = w["A005930"] - 6.0   # 산업재(20) +6%p
        try:
            tf.plan(tg, limit_idx=5); print("산업재 +6%p: 통과 (예상과 다름)")
        except ValueError as e:
            print("산업재 +6%p:", e)
        tg = dict(w); tg["A329180"] = w["A329180"] + 3.0; tg["A005930"] = w["A005930"] - 3.0   # +3%p 는 여유 안
        print("산업재 +3%p:", [(o.prod_id, round(o.wei, 2), "매도" if o.exit else "매수") for o in tf.plan(tg, limit_idx=5)])

asyncio.run(main())
