"""서버 없이 도는 단위 테스트: python -m unittest discover -v tests"""
import unittest

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


if __name__ == "__main__":
    unittest.main()
