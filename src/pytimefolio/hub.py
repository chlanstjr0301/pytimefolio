"""hub: SignalR 허브(/hubs/main)에 붙어 3 s 마다 포트폴리오를 요청한다.

근거 (보고서 2.4, 장중 측정):
- 서버는 먼저 보내지 않는다(장 전·장중 모두) -> 클라이언트가 요청해야 한다.
- 증분은 lastT 가 이 연결에서 마지막으로 받은 t 와 정확히 같을 때만 -> 요청은 한 번에 하나만.
- 열린 연결은 토큰이 만료돼도 살아 있지만, 새 negotiate 에는 유효한 토큰이 필요 -> 재연결 직전에만 토큰 확인.
- WebSocket 은 HTTP/2 를 지원하지 않아 별도 HTTP/1.1 연결을 연다 (RFC 8441 미지원).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import AsyncIterator

import ssl

import certifi
import websockets

from .auth import Auth
from .errors import BlockedError, StaleFeed
from .state import PortfolioState

log = logging.getLogger("pytimefolio.hub")
RS = "\x1e"
WS_URL = "wss://contest.timefolio.net/hubs/main"
# httpx 처럼 certifi 인증서를 쓴다. python.org macOS Python 은 기본 CA 가 비어 있어 검증에 실패한다
SSL_CTX = ssl.create_default_context(cafile=certifi.where())


class Hub:
    def __init__(self, auth: Auth, pf_id: int, interval: float = 3.0, reply_timeout: float = 20.0):
        self.a, self.pf_id, self.interval, self.reply_timeout = auth, pf_id, interval, reply_timeout
        self.ws = None
        self.state = PortfolioState()
        self._last_t = 0
        self.last_reply = 0.0                    # 마지막 응답을 받은 monotonic 시각
        self.failures = 0

    # ---------------------------------------------------------------- 연결
    async def connect(self) -> None:
        r = await self.a.call("POST", "/hubs/main/negotiate", params={"negotiateVersion": 1})
        ctoken = r.json()["connectionToken"]
        tok = await self.a.token()               # negotiate 가 401 로 갱신을 일으켰을 수 있으므로 그 뒤에 읽는다
        self.ws = await websockets.connect(f"{WS_URL}?id={ctoken}",
                                           additional_headers={"Authorization": f"Bearer {tok}"}, ssl=SSL_CTX,
                                           max_size=2 ** 22, ping_interval=None)   # SignalR 자체 ping(type 6)을 쓴다
        await self.ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
        hs = json.loads((await asyncio.wait_for(self.ws.recv(), 10)).split(RS)[0])
        if hs.get("error"):
            raise ConnectionError(f"SignalR handshake: {hs['error']}")
        self._last_t = 0                                             # 새 연결은 스냅샷부터
        log.info("허브 연결 (pfId=%s)", self.pf_id)

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    # ---------------------------------------------------------------- 요청 하나, 응답 하나
    async def poll_once(self) -> str:
        req = {"type": 1, "target": "RequestPortfolio",
               "arguments": [{"intv": int(self.interval), "auto": True, "lastT": self._last_t,
                              "d": self.a.biz_day, "pfId": self.pf_id}]}
        await self.ws.send(json.dumps(req) + RS)
        # 기한은 요청 하나에 하나: 서버 ping(15 s 마다)이 와도 늘어나지 않는다
        deadline = time.monotonic() + self.reply_timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"ReceivePortfolio 응답 없음 ({self.reply_timeout:.0f} s)")
            frame = await asyncio.wait_for(self.ws.recv(), left)
            for raw in filter(None, frame.split(RS)):
                m = json.loads(raw)
                typ = m.get("type")
                if typ == 6:                                         # ping -> pong
                    await self.ws.send(json.dumps({"type": 6}) + RS)
                elif typ == 7:                                       # 서버가 닫음
                    raise websockets.ConnectionClosedOK(None, None)
                elif typ == 1 and m.get("target") == "ReceivePortfolio":
                    p = (m.get("arguments") or [None])[0]
                    kind = self.state.apply(p if isinstance(p, dict) else None)
                    self.last_reply = time.monotonic()
                    self._last_t = self.state.t if kind != "null" else 0   # null 이면 다음은 스냅샷
                    return kind

    # ---------------------------------------------------------------- 신선도
    def age(self) -> float:
        """마지막 응답 뒤 지난 초. 한 번도 못 받았으면 무한대."""
        return time.monotonic() - self.last_reply if self.last_reply else float("inf")

    def assert_fresh(self, max_age: float | None = None) -> None:
        """마지막 응답이 max_age(기본: 폴링 2번 + 응답 대기 시간)보다 오래됐으면 StaleFeed."""
        limit = max_age if max_age is not None else 2 * self.interval + self.reply_timeout
        if self.age() > limit:
            raise StaleFeed(f"피드 마지막 응답 {self.age():.0f} s 전 (한도 {limit:.0f} s, 연속 실패 {self.failures}회)")

    # ---------------------------------------------------------------- 루프
    async def stream(self) -> AsyncIterator[tuple[str, PortfolioState]]:
        """(종류, 상태)를 3 s 마다 내보낸다. 끊기면 지수 백오프 + 지터로 다시 붙는다."""
        while True:
            try:
                if self.ws is None:
                    await self.connect()
                kind = await self.poll_once()
                self.failures = 0
            except asyncio.CancelledError:
                raise
            except BlockedError:                                      # 403/429: 다시 붙지 않고 멈춘다
                log.error("허브 요청이 차단됨 -> 재연결하지 않음")
                try:
                    await self.close()
                except Exception:
                    self.ws = None
                raise
            except Exception as e:                                    # 502, 401, 잘못된 JSON 까지 전부 재연결
                try:
                    await self.close()
                except Exception:
                    self.ws = None
                self.failures += 1
                wait = random.uniform(0, min(30.0, 2.0 ** self.failures))   # full jitter, 최대 30 s
                log.warning("허브 실패 %d회 (%s: %s) -> %.1f s 뒤 재연결",
                            self.failures, type(e).__name__, str(e)[:80], wait)
                await asyncio.sleep(wait)
                continue
            yield kind, self.state
            await asyncio.sleep(self.interval)
