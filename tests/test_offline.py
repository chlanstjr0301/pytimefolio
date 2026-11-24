"""서버 없이 도는 단위 테스트: python -m unittest -v"""
import unittest

from pytimefolio.orders import Order, cap_weights, round_to_tick, sector_violations, target_to_orders, tick_size
from pytimefolio.state import PortfolioState


def snap(t=100):
    return {"t": t, "kstNow": "2026-11-24 09:35:00", "bs": {"nav": 1},
            "pos": [{"Id": 1, "prodId": "A005930", "wei": 9.5, "close": 189900, "avgPrc": 176812, "pos": 417},
                    {"Id": 2, "prodId": "A079550", "wei": 0.43, "close": 351000, "avgPrc": 361561.2, "pos": 10}],
            "ords": [{"Id": 1649882, "prodId": "A079550", "state": "Working", "cumQty": 6, "qty2ord": 7}],
            "msecs": []}


class StateTest(unittest.TestCase):
    def test_snapshot_then_diff(self):
        s = PortfolioState()
        self.assertEqual(s.apply(snap()), "full")
        d = {"t": 103, "pos": None, "ords": None, "bs": None, "msecs": None,
             "pos_diff": {"added": [], "removed": [], "updated": {"1": {"close": 190000, "wei": 9.51}}, "ords_chgd": {}},
             "ords_diff": {"added": [], "removed": [], "updated": {"1649882": {"cumQty": 7, "state": "Done"}}, "ords_chgd": {}}}
        self.assertEqual(s.apply(d), "diff")
        self.assertEqual(s.pos[1]["close"], 190000)
        self.assertEqual(s.pos[1]["avgPrc"], 176812)          # 바뀌지 않은 필드는 남는다
        self.assertNotIn("1", s.pos)                           # 문자열 키가 따로 생기지 않는다
        self.assertEqual(s.ords[1649882]["state"], "Done")
        self.assertEqual(s.t, 103)
        self.assertEqual(s.open_orders(), [])

    def test_null_keeps_state(self):
        s = PortfolioState(); s.apply(snap())
        self.assertEqual(s.apply(None), "null")
        self.assertEqual(len(s.pos), 2)

    def test_added_removed(self):
        s = PortfolioState(); s.apply(snap())
        s.apply({"t": 106, "pos": None, "pos_diff": {"added": [{"Id": 3, "prodId": "A000660", "wei": 1}],
                                                     "removed": [2], "updated": {}}})
        self.assertEqual(sorted(s.by_prod()), ["A000660", "A005930"])

    def test_snapshot_replaces(self):
        s = PortfolioState(); s.apply(snap())
        s.apply({**snap(200), "pos": [{"Id": 9, "prodId": "A000270", "wei": 3}]})
        self.assertEqual(list(s.by_prod()), ["A000270"])


class OrderMathTest(unittest.TestCase):
    def test_cap_weights_keeps_total(self):
        w = cap_weights({"A": 20, "B": 10, "C": 10}, {"A": 15})
        self.assertEqual(w, {"A": 15, "B": 12.5, "C": 12.5})
        w = cap_weights({"A": 20, "B": 14, "C": 6}, {"A": 15, "B": 15})
        self.assertAlmostEqual(sum(w.values()), 40)
        self.assertLessEqual(w["B"], 15 + 1e-9)

    def test_target_to_orders(self):
        o = target_to_orders({"X": 5, "Y": 2}, {"X": 4.97, "Y": 3, "Z": 1}, limit_idx=5)
        self.assertEqual([(x.prod_id, x.exit) for x in o], [("Y", True), ("Z", True)])   # X 차이 0.03 < 0.05
        self.assertAlmostEqual(o[0].wei, 1.0)

    def test_order_body(self):
        b = Order("A005930", 0.1, limit_prc=151800).body("2026-11-24", 27032, "09:18")
        self.assertEqual(b, {"d": "2026-11-24", "pfId": 27032, "prodId": "A005930", "ls": "L", "ex": "E",
                             "wei": 0.1, "exitAll": False, "limitPrc": 151800, "limitIdx": None,
                             "stopPrc": None, "hm0": "09:18", "hm1": None})   # 09:18 실제 캡처와 같은 형태
        with self.assertRaises(ValueError):
            Order("A005930", 0.1).body("2026-11-24", 1, "09:00")

    def test_sector_violations(self):
        sec = {"A": "20", "B": "20", "C": "45", "D": "25"}
        mkt = {"20": 17.7, "45": 52.9, "25": 5.75}          # 한도: 20 -> 35.4, 45 -> 105.8, 25 -> 11.5
        self.assertEqual(sector_violations({"A": 20, "B": 15, "C": 50, "D": 11}, sec, mkt), {})
        self.assertEqual(sector_violations({"A": 20, "B": 16, "D": 12}, sec, mkt),
                         {"20": (36.0, 35.4), "25": (12.0, 11.5)})
        self.assertEqual(sector_violations({"D": 10}, sec, {}), {})          # 시장 비중이 작아도 10% 까지는 허용
        with self.assertRaises(KeyError):
            sector_violations({"Z": 1}, sec, mkt)

    def test_ticks(self):
        self.assertEqual(round_to_tick(151840), 151800)
        self.assertEqual(round_to_tick(224800), 224500)
        self.assertEqual(tick_size(1500), 1)
        self.assertEqual(tick_size(1_171_000), 1000)


if __name__ == "__main__":
    unittest.main()
