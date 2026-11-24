"""가이드라인과 1주 필터 확인 (주문 없음). 원본 구조도 같이 찍어서 필드 이름을 맞춘다."""
import asyncio, json, logging
from pytimefolio import Timefolio

async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Timefolio(ctst_id=91) as tf:
        st = tf.state
        print("최상위 기타 필드:", sorted(st.extra))
        print("cmpl:", json.dumps(st.extra.get("cmpl"), ensure_ascii=False))
        print("tags:", json.dumps(st.extra.get("tags"), ensure_ascii=False)[:200])
        print("bs 키:", sorted((st.bs or {}).keys()))
        m = next(iter(st.msecs.values()), None)
        print("msecs", len(st.msecs), "개, 한 줄:", json.dumps(m, ensure_ascii=False)[:300])
        print(f"NAV 역산: {tf.nav():,.0f}원 | 1주 비중: 삼성 {tf.share_weight('A005930'):.4f}%  LIG {tf.share_weight('A079550'):.4f}%  하이닉스 {tf.share_weight('A000660'):.4f}%")
        g = await tf.guidelines()
        print("server Violations:", json.dumps(g.pop("server"), ensure_ascii=False)[:600])
        print("섹터:", {k: v for k, v in g["sector"].items()})
        print("소형주:", g["small_cap"], "| 회전율:", g["turnover"], "| ok:", g["ok"])
        print("bs.na =", f"{(st.bs or {}).get('na'):,}" if isinstance((st.bs or {}).get('na'), (int, float)) else (st.bs or {}).get('na'), "| bs.nav =", (st.bs or {}).get("nav"))
        w = tf.weights()
        tg = dict(w); tg["A000660"] = w["A000660"] + 0.03; tg["A005930"] = w["A005930"] + 0.1; tg["A058470"] = w["A058470"] + 0.06
        print("1주 필터 계획:", [(o.prod_id, round(o.wei, 3)) for o in tf.plan(tg, min_wei=0.01, limit_idx=5)],
              "| 뺀 것:", [(o.prod_id, round(o.wei, 3)) for o in tf.dropped])

asyncio.run(main())
