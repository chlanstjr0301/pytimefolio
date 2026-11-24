"""qty2ord 가 내림인지 반올림인지 판별 (실주문 1건, 사용자 승인 필요).
삼성전자 0.04% = 1주 비중 약 0.0226% 의 1.77배 -> 내림이면 1주, 반올림이면 2주.
현재가 -20% 지정가로 넣고 3 s 뒤 정지한다."""
import asyncio, logging
from pytimefolio import Timefolio
from pytimefolio.orders import round_to_tick

async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Timefolio(ctst_id=91) as tf:
        sw = tf.share_weight("A005930")
        px = round_to_tick(tf.positions()["A005930"]["close"] * 0.8)
        print(f"1주 비중 {sw:.4f}% -> 0.04% 는 {0.04/sw:.2f}주어치 | 지정가 {px}")
        oid = await tf.buy("A005930", 0.04, limit_prc=px)
        q = tf.state.ords[oid].get("qty2ord")
        print(f"qty2ord = {q} -> {'내림' if q == int(0.04/sw) else '반올림' if q == round(0.04/sw) else '기타'}")
        await asyncio.sleep(3)
        await tf.stop(oid)
        r = await tf.wait(oid, timeout=30)
        print("결과:", r.outcome, f"{r.cum_qty}/{r.qty2ord}", "| 남은 한도", tf.orders_left())

asyncio.run(main())
