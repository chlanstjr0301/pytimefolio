"""orders 실주문 테스트 (사용자 승인: 삼성전자 0.1%, 현재가 -20% 지정가, 넣자마자 정지).
피드를 백그라운드로 돌리면서 place -> stop -> wait_done 을 한 번 한다."""
import asyncio, logging
from pytimefolio.transport import Transport
from pytimefolio.auth import Auth
from pytimefolio.rest import Rest
from pytimefolio.hub import Hub
from pytimefolio.orders import Orders, Order, round_to_tick, cap_weights, target_to_orders

async def feed(hub, ready):
    async for kind, st in hub.stream():
        ready.set()

async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Transport() as t:
        a = Auth(t); await a.start()
        r = Rest(a); pf = r.select_portfolio(91)
        hub = Hub(a, pf); ready = asyncio.Event()
        task = asyncio.create_task(feed(hub, ready))
        await ready.wait()
        od = Orders(r, hub)

        # 주문 없이 확인: 목표 -> 주문 변환
        cur = {p: float(x.get("wei") or 0) for p, x in hub.state.by_prod().items()}
        tg = dict(cur); tg["A005930"] = cur["A005930"] + 0.2; tg["A079550"] = 0.0
        print("변환 예시:", [(o.prod_id, round(o.wei, 2), "매도" if o.exit else "매수") for o in target_to_orders(tg, cur, limit_idx=5)])
        print("water-filling 예시:", cap_weights({"A": 20, "B": 10, "C": 10}, {"A": 15}))
        print("오늘 사용:", od.used_today(), "건 (로컬 기록, 오늘 앞선 주문 8건은 포함 안 됨)")

        close = hub.state.by_prod()["A005930"]["close"]
        px = round_to_tick(close * 0.8)
        print(f"삼성 현재가 {close} -> 지정가 {px}")
        oid = await od.place(Order("A005930", 0.1, limit_prc=px))
        await asyncio.sleep(3)
        await od.stop([oid])
        res = await od.wait_done(oid, timeout=30)
        print("결과:", res)
        print("오늘 사용:", od.used_today(), "건")
        task.cancel(); await hub.close()

asyncio.run(main())
