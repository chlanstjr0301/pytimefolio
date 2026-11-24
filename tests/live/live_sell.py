"""매도(ex=X) 실측 (사용자 승인: LIG 0.1%, 현재가 +20% 지정가, 넣자마자 정지)."""
import asyncio, logging
from pytimefolio import Timefolio
from pytimefolio.orders import round_to_tick

async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Timefolio(ctst_id=91) as tf:
        lig = tf.positions()["A079550"]
        px = round_to_tick(lig["close"] * 1.2, down=False)
        print(f"LIG 보유 {lig['pos']}주 {lig['wei']}% | 현재가 {lig['close']} -> 매도 지정가 {px}")
        oid = await tf.sell("A079550", 0.1, limit_prc=px)
        rec = tf.state.ords[oid]
        print("주문 기록:", {k: rec.get(k) for k in ("Id", "prodId", "ls", "ex", "sgn", "wei", "qty2ord", "state", "limitPrc", "hm0", "hm1")})
        await asyncio.sleep(3)
        await tf.stop(oid)
        res = await tf.wait(oid, timeout=30)
        print("결과:", res.outcome, f"{res.cum_qty}/{res.qty2ord}", "| 남은 한도", tf.orders_left())

asyncio.run(main())
