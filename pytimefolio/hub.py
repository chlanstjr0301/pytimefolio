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
from typing import AsyncIterator

import websockets

from .auth import Auth
from .state import PortfolioState

log = logging.getLogger("pytimefolio.hub")
RS = "\x1e"
WS_URL = "wss://contest.timefolio.net/hubs/main"


class Hub:
    def __init__(self, auth: Auth, pf_id: int, interval: float = 3.0, reply_timeout: float = 20.0):
        self.a, self.pf_id, self.interval, self.reply_timeout = auth, pf_id, interval, reply_timeout
        self.ws = None
        self.state = PortfolioState()
        self._last_t = 0

    # ---------------------------------------------------------------- 연결
    async def connect(self) -> None:
        tok = await self.a.token()                                   # 만료됐으면 여기서 갱신된다
        r = await self.a.call("POST", "/hubs/main/negotiate", params={"negotiateVersion": 1})
        ctoken = r.json()["connectionToken"]
        self.ws = await websockets.connect(f"{WS_URL}?id={ctoken}",
                                           additional_headers={"Authorization": f"Bearer {tok}"},
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
        while True:
            frame = await asyncio.wait_for(self.ws.recv(), self.reply_timeout)
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
                    self._last_t = self.state.t if kind != "null" else 0   # null 이면 다음은 스냅샷
                    return kind

    # ---------------------------------------------------------------- 루프
    async def stream(self) -> AsyncIterator[tuple[str, PortfolioState]]:
        """(종류, 상태)를 3 s 마다 내보낸다. 끊기면 지수 백오프 + 지터로 다시 붙는다."""
        failures = 0
        while True:
            try:
                if self.ws is None:
                    await self.connect()
                kind = await self.poll_once()
                failures = 0
                yield kind, self.state
                await asyncio.sleep(self.interval)
            except (websockets.ConnectionClosed, asyncio.TimeoutError, OSError, ConnectionError) as e:
                await self.close()
                failures += 1
                wait = random.uniform(0, min(30.0, 2.0 ** failures))   # full jitter, 최대 30 s
                log.warning("허브 끊김 (%s) -> %.1f s 뒤 재연결", type(e).__name__, wait)
                await asyncio.sleep(wait)
