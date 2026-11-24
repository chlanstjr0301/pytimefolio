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
from pytimefolio.errors import AmbiguousState, AuthError, BlockedError, RequestError, ServerError, StaleFeed
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

    def test_cookie_expiry_survives_restart(self):
        """저장 -> 복원 -> 다시 저장해도 refreshToken 만료 시각이 남는다 (D5 재로그인 판단에 필요)."""
        exp = int(time.time()) + 6 * 86400
        self.session.write_text(json.dumps([{"name": "refreshToken", "value": "v", "domain": "contest.timefolio.net",
                                             "path": "/", "expires": exp}]))
        a = Auth(Transport(transport=httpx.MockTransport(lambda r: httpx.Response(200))), session_file=self.session)
        self.assertTrue(a._load_cookies())
        self.assertEqual(a.refresh_expires, exp)
        a._save_cookies()
        self.assertEqual(json.loads(self.session.read_text())[0]["expires"], exp)

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
        for code in (403, 429):
            with self.subTest(code=code):
                od, _ = self.make(lambda r, c=code: httpx.Response(c))
                with self.assertRaises(BlockedError):
                    run(od._write("AddOrder", {}))
                self.assertIsNotNone(od.halted)
                od.r.a.t._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
                with self.assertRaises(BlockedError):    # 서버가 다시 받아 줘도 래퍼는 멈춘 상태
                    run(od._write("AddOrder", {}))

    def test_writes_spaced_at_least_1s(self):
        """기본 간격(1 s)으로 쓰기 3건을 동시에 요청해도 서버에 닿는 시각이 1 s 이상 벌어진다."""
        stamps = []
        def h(req):
            stamps.append(time.monotonic())
            return httpx.Response(200, json={})
        hub = FakeHub()
        od = Orders(FakeRest(FakeAuth(h)), hub)          # min_interval 기본값 1.0
        async def go():
            await asyncio.gather(*(od._write("AddOrder", {}) for _ in range(3)))
        run(go())
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(stamps), 3)
        self.assertTrue(all(g >= 0.99 for g in gaps), gaps)

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

    def test_failed_resend_is_searched_not_raised(self):
        """연결 실패 -> 한 번 다시 보냄 -> 그것도 실패해도 ServerError 가 아니라 찾기를 거쳐 AmbiguousState."""
        n = {"add": 0}
        def h(req):
            n["add"] += 1
            raise httpx.ConnectError("down", request=req)
        od, _ = self.make(h)
        async def quick_find(o, before, timeout): return None
        od._find = quick_find
        with mock.patch.object(orders_mod.asyncio, "sleep", new=mock.AsyncMock()):
            with self.assertRaises(AmbiguousState):
                run(od.place(Order("A005930", 0.1, limit_prc=150000)))
        self.assertEqual(n["add"], 2)                    # 처음 + 한 번 다시, 세 번째는 없다

    def test_same_order_twice_gets_two_ids(self):
        """주문 1 의 ID 가 피드에 늦게 나타나도 같은 내용의 주문 2 가 그 ID 를 가져가지 않는다."""
        od, hub = self.make(lambda r: httpx.Response(200, json={"data": ""}))
        rec = {"prodId": "A005930", "ls": "L", "ex": "E", "wei": 0.1, "state": "Working"}
        seq = iter([{**rec, "Id": 11}, {**rec, "Id": 12}])
        async def rest_orders(*a): return [next(seq)]   # 주문 1 은 REST 로만 확인된다
        od.r.orders = rest_orders
        async def no_feed(o, before, timeout): return None
        od._find = no_feed
        first = run(od.place(Order("A005930", 0.1, limit_prc=150000)))
        async def late_feed(o, before, timeout):       # 주문 2 를 찾는 동안에야 피드에 주문 1 이 나타난다
            hub.state.ords[11] = {**rec, "Id": 11}
            return od._match(o, before)
        od._find = late_feed
        second = run(od.place(Order("A005930", 0.1, limit_prc=150000)))
        self.assertEqual(first, 11)
        self.assertEqual(second, 12)

    def test_block_on_read_halts_writes(self):
        """읽기에서 받은 403 도 이후 쓰기를 막는다."""
        od, _ = self.make(lambda r: httpx.Response(403))
        with self.assertRaises(BlockedError):
            run(od.r.a.call("GET", "/api/Portfolio/Orders"))
        od.r.a.t._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        with self.assertRaises(BlockedError):
            run(od._write("AddOrder", {}))


class FakeWS:
    """send 는 버리고, recv 는 주어진 프레임을 차례로 돌려준 뒤 영원히 기다린다."""
    def __init__(self, frames, gap):
        self.frames, self.gap = list(frames), gap
    async def send(self, _): pass
    async def recv(self):
        if self.frames:
            await asyncio.sleep(self.gap)
            return self.frames.pop(0)
        await asyncio.sleep(3600)
    async def close(self): pass


class HubTimeoutTest(unittest.TestCase):
    def test_pings_do_not_extend_reply_deadline(self):
        """응답 없이 ping 만 계속 와도 요청 하나의 기한(reply_timeout)이 지나면 TimeoutError."""
        h = Hub.__new__(Hub)
        h.interval, h.reply_timeout, h._last_t, h.pf_id = 3.0, 0.5, 0, 1
        h.a = mock.Mock(biz_day="2026-11-24")
        h.state = PortfolioState()
        ping = json.dumps({"type": 6}) + "\x1e"
        h.ws = FakeWS([ping] * 20, gap=0.2)            # 0.2 s 마다 ping: 프레임마다 기한을 새로 잡으면 끝나지 않는다
        t0 = time.monotonic()
        with self.assertRaises((TimeoutError, asyncio.TimeoutError)):
            run(h.poll_once())
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_blocked_stops_reconnecting(self):
        h = Hub.__new__(Hub)
        h.interval, h.reply_timeout, h.failures, h.ws = 3.0, 20.0, 0, None
        calls = {"n": 0}
        async def connect():
            calls["n"] += 1
            raise BlockedError("403 POST /hubs/main/negotiate")
        h.connect = connect
        async def go():
            async for _ in h.stream():
                pass
        with self.assertRaises(BlockedError):
            run(go())
        self.assertEqual(calls["n"], 1)


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
