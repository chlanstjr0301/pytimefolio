"""Timefolio: 전략 코드가 쓰는 진입점. 연결, 인증, 피드, 주문을 하나로 묶는다.

    async with Timefolio(ctst_id=91) as tf:
        print(tf.weights())
        ids = await tf.rebalance({"A005930": 10.0}, limit_idx=5, hm0="09:00", hm1="09:30")
        results = [await tf.wait(i) for i in ids]
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .auth import Auth, DEFAULT_SESSION
from .errors import TimefolioError
from .hub import Hub
from .orders import Order, Orders, Result, cap_weights, target_to_orders
from .rest import Rest
from .state import PortfolioState
from .transport import Transport

log = logging.getLogger("pytimefolio")


class Timefolio:
    def __init__(self, ctst_id: int, email: str | None = None, password: str | None = None,
                 session_file: Path | str = DEFAULT_SESSION, daily_cap: int = 80,
                 allow_untested: bool = False, feed_interval: float = 3.0):
        self.ctst_id = ctst_id
        self.t = Transport()
        self.auth = Auth(self.t, email, password, session_file)
        self.rest = Rest(self.auth)
        self._daily_cap, self._allow_untested, self._interval = daily_cap, allow_untested, feed_interval
        self.hub: Hub | None = None
        self.orders: Orders | None = None
        self._feed_task: asyncio.Task | None = None
        self._first = asyncio.Event()

    # ---------------------------------------------------------------- 수명
    async def __aenter__(self) -> "Timefolio":
        await self.auth.start()
        pf = self.rest.select_portfolio(self.ctst_id)
        self.hub = Hub(self.auth, pf, interval=self._interval)
        self.orders = Orders(self.rest, self.hub, self._daily_cap, allow_untested=self._allow_untested)
        self._feed_task = asyncio.create_task(self._feed(), name="pytimefolio-feed")
        await asyncio.wait_for(self._first.wait(), 30)
        n = await self._sync_count()
        log.info("준비 완료: pfId=%s, 영업일 %s, 오늘 주문 %d/%d건", pf, self.rest.biz_day, n, self._daily_cap)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._feed_task:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.hub:
            await self.hub.close()
        await self.t.aclose()

    async def _feed(self) -> None:
        async for _kind, _st in self.hub.stream():
            self._first.set()

    async def _sync_count(self) -> int:
        """오늘 서버에 남은 주문(신규 + 정지)을 세서 하루 한도 계산에 반영한다."""
        ords = await self.rest.orders()
        server = len(ords) + sum(1 for o in ords if o.get("cnclT"))
        return self.orders.sync_count(server)

    def _alive(self) -> None:
        if self._feed_task and self._feed_task.done():
            raise TimefolioError(f"피드가 멈춤: {self._feed_task.exception()!r}")

    # ---------------------------------------------------------------- 상태
    @property
    def state(self) -> PortfolioState:
        self._alive()
        return self.hub.state

    def weights(self) -> dict[str, float]:
        """종목코드 -> 현재 비중(%)."""
        return {p: float(r.get("wei") or 0) for p, r in self.state.by_prod().items()}

    def positions(self) -> dict[str, dict]:
        return self.state.by_prod()

    def open_orders(self) -> list[dict]:
        return self.state.open_orders()

    def orders_left(self) -> int:
        return self._daily_cap - self.orders.used_today()

    # ---------------------------------------------------------------- 조회
    async def universe(self) -> list[dict]:
        return await self.rest.universe()

    async def session(self) -> dict:
        return await self.rest.session()

    # ---------------------------------------------------------------- 주문
    async def buy(self, prod_id: str, wei: float, **price) -> int:
        """wei: 주문 크기(%). price: limit_prc= / limit_idx= / stop_prc=, hm0=, hm1="""
        self._alive()
        return await self.orders.place(Order(prod_id, wei, exit=False, **price))

    async def sell(self, prod_id: str, wei: float, **price) -> int:
        self._alive()
        return await self.orders.place(Order(prod_id, wei, exit=True, **price))

    async def stop(self, *order_ids: int) -> None:
        await self.orders.stop(list(order_ids))

    async def wait(self, order_id: int, timeout: float | None = None) -> Result:
        return await self.orders.wait_done(order_id, timeout)

    def plan(self, targets: dict[str, float], min_wei: float = 0.05, **price) -> list[Order]:
        """목표 비중(%) -> 주문 목록 (보내지 않는다). 보유 종목의 weiLimit 으로 먼저 자른다."""
        caps = {p: float(r["weiLimit"]) for p, r in self.positions().items() if r.get("weiLimit") is not None}
        capped = cap_weights(targets, {k: v for k, v in caps.items() if k in targets})
        return target_to_orders(capped, self.weights(), min_wei=min_wei, **price)

    async def rebalance(self, targets: dict[str, float], min_wei: float = 0.05, **price) -> list[int]:
        """plan() 의 주문을 1 s 간격으로 보내고 주문 ID 목록을 돌려준다. 한도가 모자라면 시작하지 않는다."""
        orders = self.plan(targets, min_wei, **price)
        if len(orders) > self.orders_left():
            raise TimefolioError(f"주문 {len(orders)}건 > 남은 한도 {self.orders_left()}건")
        ids = []
        for o in orders:
            ids.append(await self.orders.place(o))
        return ids
