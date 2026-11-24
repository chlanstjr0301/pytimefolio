"""실패 경로 테스트 (서버 없이, httpx.MockTransport 와 가짜 객체로): python -m unittest discover -v tests"""
import asyncio
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from pytimefolio import orders as orders_mod
from pytimefolio.auth import Auth
from pytimefolio.errors import AuthError, BlockedError, RequestError, ServerError, StaleFeed
from pytimefolio.hub import Hub
from pytimefolio.orders import Order, Orders, _same_order, quantize, remaining_wei, target_to_orders
from pytimefolio.state import PortfolioState
from pytimefolio.transport import Transport


def jwt(iat: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"iat": iat, "exp": iat + 900}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def run(coro):
    return asyncio.run(coro)


class TransportTest(unittest.TestCase):
    def call(self, handler, method="GET"):
        async def go():
            async with Transport(transport=httpx.MockTransport(handler)) as t:
                return await t.request(method, "/api/x")
        return run(go())

    def test_status_mapping(self):
        for code, exc in ((401, AuthError), (403, BlockedError), (429, BlockedError), (400, RequestError), (502, ServerError)):
            with self.assertRaises(exc, msg=code):
                self.call(lambda r, c=code: httpx.Response(c))

    def test_get_retried_once_post_not(self):
        n = {"GET": 0, "POST": 0}
        def h(req):
            n[req.method] += 1
            raise httpx.ConnectError("down", request=req)
        with self.assertRaises(ServerError) as cm:
            self.call(h, "GET")
        self.assertEqual(n["GET"], 2)
        with self.assertRaises(ServerError) as cm:
            self.call(h, "POST")
        self.assertEqual(n["POST"], 1)
        self.assertFalse(cm.exception.sent)          # 연결 실패: 서버에 가지 않았음

    def test_read_error_counts_as_sent(self):
        def h(req):
            raise httpx.ReadError("cut", request=req)
        with self.assertRaises(ServerError) as cm:
            self.call(h, "POST")
        self.assertTrue(cm.exception.sent)


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "s.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_flight_renewal(self):
        calls = {"refresh": 0}
        def h(req):
            if req.url.path.endswith("SilentRefresh"):
                calls["refresh"] += 1
                return httpx.Response(200, json={"accessToken": jwt(int(time.time())), "currBizDay": "2026-11-24"})
            return httpx.Response(200, json={})
        async def go():
            async with Transport(transport=httpx.MockTransport(h)) as t:
                a = Auth(t, session_file=self.session)
                a._token, a._iat = jwt(0), 0.0           # 오래전에 발급된 토큰: 갱신이 필요
                toks = await asyncio.gather(*(a.token() for _ in range(5)))
                return toks
        toks = run(go())
        self.assertEqual(calls["refresh"], 1)            # 5개가 동시에 불러도 갱신은 한 번
        self.assertEqual(len(set(toks)), 1)

    def test_401_retry_refreshes_once(self):
        calls = {"refresh": 0, "x": 0}
        fresh = jwt(int(time.time()))
        def h(req):
            if req.url.path.endswith("SilentRefresh"):
                calls["refresh"] += 1
                return httpx.Response(200, json={"accessToken": fresh, "currBizDay": "2026-11-24"})
            calls["x"] += 1
            ok = req.headers.get("authorization") == f"Bearer {fresh}"
            return httpx.Response(200 if ok else 401, json={})
        async def go():
            async with Transport(transport=httpx.MockTransport(h)) as t:
                a = Auth(t, session_file=self.session)
                a._token, a._iat = jwt(int(time.time()) - 5), time.time()   # 아직 유효해 보이지만 서버는 거절
                await asyncio.gather(a.call("GET", "/api/x"), a.call("GET", "/api/x"))
        run(go())
        self.assertEqual(calls["refresh"], 1)
        self.assertIn(calls["x"], (3, 4))                # 거절된 요청만 한 번씩 다시 보냄

    def test_corrupt_session_file_falls_back(self):
        self.session.write_text("{broken")
        a = Auth(Transport(transport=httpx.MockTransport(lambda r: httpx.Response(200))), session_file=self.session)
        self.assertFalse(a._load_cookies())


class OrderMathFailureTest(unittest.TestCase):
    def test_quantized_size_matches_feed(self):
        o = Order("A005930", 0.623456, limit_idx=5)
        self.assertEqual(o.wei, 0.62)
        self.assertEqual(o.body("2026-11-24", 1, "09:00")["wei"], 0.62)
        self.assertTrue(_same_order({"prodId": "A005930", "ex": "E", "wei": 0.62}, o))
        self.assertFalse(_same_order({"prodId": "A005930", "ex": "E", "wei": 0.63}, o))
        with self.assertRaises(ValueError):
            Order("A005930", 0.004, limit_idx=5)
        self.assertEqual(quantize(0.1 + 0.2), 0.3)

    def test_pending_orders_not_repeated(self):
        rec = {"prodId": "A", "ex": "E", "wei": 0.6, "qty2ord": 10, "cumQty": 4}
        self.assertAlmostEqual(remaining_wei(rec), 0.36)
        self.assertAlmostEqual(remaining_wei({**rec, "ex": "X"}), -0.36)
        # 목표 +1.0 인데 0.36 이 이미 나가 있으면 0.64 만 더 주문
        o = target_to_orders({"A": 2.0}, {"A": 1.0}, pending={"A": 0.36}, limit_idx=5)
        self.assertEqual([(x.prod_id, x.wei, x.exit) for x in o], [("A", 0.64, False)])
        self.assertEqual(target_to_orders({"A": 2.0}, {"A": 1.0}, pending={"A": 1.0}, limit_idx=5), [])


class FakeRest:
    biz_day = "2026-11-24"
    def __init__(self, a): self.a = a
    def _pf(self): return 1
    def biz_day_is_today(self): return True
    def kst_today(self): return self.biz_day
    async def validate_product(self, *a, **k): return []
    async def orders(self, *a): return []


class FakeAuth:
    def __init__(self, handler):
        self.t = Transport(transport=httpx.MockTransport(handler))
    async def call(self, method, path, **kw):
        return await self.t.request(method, path, **kw)


class FakeHub:
    def __init__(self): self.state, self.fresh = PortfolioState(), True
    def assert_fresh(self):
        if not self.fresh: raise StaleFeed("stale")


class OrdersGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(orders_mod, "STATE_DIR", Path(self.tmp.name))
        self.p.start()

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup()

    def make(self, handler, cap=80):
        hub = FakeHub()
        return Orders(FakeRest(FakeAuth(handler)), hub, daily_cap=cap, min_interval=0.0), hub

    def test_count_and_cap(self):
        od, _ = self.make(lambda r: httpx.Response(200, json={}), cap=3)
        run(od._write("AddOrder", {}))
        run(od._write("CancelOrderBatch", {}, count=2))
        self.assertEqual(od.used_today(), 3)
        with self.assertRaises(Exception):
            run(od._write("AddOrder", {}))
        self.assertEqual(od.sync_count(2), 3)            # 서버 기록이 작으면 로컬 값 유지
        self.assertEqual(od.sync_count(5), 5)

    def test_connect_failure_not_counted(self):
        def h(req): raise httpx.ConnectError("down", request=req)
        od, _ = self.make(h)
        with self.assertRaises(ServerError):
            run(od._write("AddOrder", {}))
        self.assertEqual(od.used_today(), 0)

    def test_blocked_halts_all_writes(self):
        od, _ = self.make(lambda r: httpx.Response(429))
        with self.assertRaises(BlockedError):
            run(od._write("AddOrder", {}))
        self.assertIsNotNone(od.halted)
        od.r.a.t._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        with self.assertRaises(BlockedError):            # 서버가 다시 받아 줘도 래퍼는 멈춘 상태
            run(od._write("AddOrder", {}))

    def test_lost_reply_is_not_resent(self):
        n = {"add": 0}
        def h(req):
            n["add"] += 1
            raise httpx.ReadError("cut", request=req)    # 보냈지만 응답이 끊김
        od, hub = self.make(h)
        orders_mod_find = od._find
        async def quick_find(o, before, timeout): return await orders_mod_find(o, before, 0.1)
        od._find = quick_find
        with self.assertRaises(Exception) as cm:
            run(od.place(Order("A005930", 0.1, limit_prc=150000)))
        self.assertEqual(n["add"], 1)                    # 다시 보내지 않았다
        self.assertIn("확인하지 못함", str(cm.exception))

    def test_stale_feed_blocks_orders_and_waits(self):
        od, hub = self.make(lambda r: httpx.Response(200, json={}))
        hub.fresh = False
        with self.assertRaises(StaleFeed):
            run(od.place(Order("A005930", 0.1, limit_prc=150000)))
        with self.assertRaises(StaleFeed):
            run(od.wait_done(123))


class HubFreshnessTest(unittest.TestCase):
    def test_assert_fresh(self):
        h = Hub.__new__(Hub)
        h.interval, h.reply_timeout, h.failures, h.last_reply = 3.0, 20.0, 0, 0.0
        with self.assertRaises(StaleFeed):               # 한 번도 응답을 못 받음
            h.assert_fresh()
        h.last_reply = time.monotonic()
        h.assert_fresh()
        h.last_reply = time.monotonic() - 30
        with self.assertRaises(StaleFeed):
            h.assert_fresh()


if __name__ == "__main__":
    unittest.main()
