"""rest 동작 확인. RFM 13회 ctstId=91. 개인정보는 출력하지 않는다."""
import asyncio, logging
from collections import Counter
from pytimefolio.transport import Transport
from pytimefolio.auth import Auth
from pytimefolio.rest import Rest

async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Transport() as t:
        a = Auth(t); await a.start()          # 저장된 세션으로만 (로그인 정보 없이)
        r = Rest(a)
        print("포트폴리오:", r.select_portfolio(91), "| 영업일이 오늘?", r.biz_day_is_today())
        s, u, tr, od = await asyncio.gather(r.session(), r.universe(), r.trades(), r.orders())
        print("Session:", s)
        print("Universe:", len(u), "종목 | 섹터별", dict(Counter(x["sec"] for x in u).most_common(4)))
        print("Trades:", len(tr), "건")
        print("오늘 주문:", [(o["prodId"], o["wei"], o["state"], o.get("cnclT") is not None, f'{o["cumQty"]}/{o["qty2ord"]}') for o in od])
        print("ValidateProduct A005930:", await r.validate_product("A005930"))

asyncio.run(main())
