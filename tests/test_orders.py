"""서버 없이 도는 단위 테스트: python -m unittest discover -v tests"""
import unittest

from pytimefolio.orders import Order, cap_weights, round_to_tick, sector_violations, target_to_orders, tick_size


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
        b = Order("A005930", 0.1, limit_prc=151800).body("2026-11-24", 12345, "09:18")
        self.assertEqual(b, {"d": "2026-11-24", "pfId": 12345, "prodId": "A005930", "ls": "L", "ex": "E",
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
