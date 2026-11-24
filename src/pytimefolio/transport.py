"""transport: HTTP/2 연결 하나와 서버 시계 오프셋을 관리한다.

근거 (보고서 2.1, 2.2):
- 새 연결 106.5 ms, 재사용 16.0 ms -> 프로세스 동안 클라이언트 하나를 유지한다.
- 서버는 15~30 s 놀면 연결을 끊는다 -> keepalive_expiry 를 그보다 짧은 10 s 로 둔다.
- PC 시계가 서버보다 3 s 빨랐다 -> 응답의 Date 헤더로 오프셋을 잰다.
"""
from __future__ import annotations

import statistics
import time
from collections import deque
from email.utils import parsedate_to_datetime

import httpx

from .errors import AuthError, BlockedError, RequestError, ServerError

BASE_URL = "https://contest.timefolio.net"


class Transport:
    """async with Transport() as t: ... 로 쓴다."""

    def __init__(self, base_url: str = BASE_URL, timeout: float = 15.0, keepalive_expiry: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        """transport: 테스트용 (httpx.MockTransport). 평소에는 None."""
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            http2=transport is None,
            timeout=timeout,
            limits=httpx.Limits(keepalive_expiry=keepalive_expiry),
            event_hooks={"response": [self._on_response]},
        )
        self._offsets: deque[float] = deque(maxlen=9)   # 최근 9개 표본의 중앙값을 쓴다

    # ---------------------------------------------------------------- 수명
    async def __aenter__(self) -> "Transport":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def cookies(self) -> httpx.Cookies:
        """로그인과 갱신 때 서버가 준 쿠키. auth 가 저장하고 복원한다."""
        return self._client.cookies

    async def warm_up(self) -> None:
        """첫 요청의 준비 비용(모듈 로딩, SSL 컨텍스트, 연결 약 1.3 s)을 미리 낸다.
        인증이 필요 없는 가벼운 요청 하나를 보내고 401은 무시한다. 시계 표본도 하나 생긴다."""
        try:
            await self.request("GET", "/api/Portfolio/Session", params={"d": "2000-01-01"})
        except AuthError:
            pass

    # ---------------------------------------------------------------- 서버 시계
    async def _on_response(self, resp: httpx.Response) -> None:
        """Date 헤더(초 단위)와 응답을 받은 로컬 시각의 차이를 표본으로 쌓는다."""
        date = resp.headers.get("date")
        if not date:
            return
        server = parsedate_to_datetime(date).timestamp()
        # Date 는 초 단위로 잘려 있으므로 평균적으로 0.5 s 이른 값이다.
        self._offsets.append(server + 0.5 - time.time())

    @property
    def clock_offset(self) -> float:
        """서버 시각 - 로컬 시각 (초). 표본이 없으면 0."""
        return statistics.median(self._offsets) if self._offsets else 0.0

    def server_time(self) -> float:
        """서버 기준 현재 Unix 시각."""
        return time.time() + self.clock_offset

    # ---------------------------------------------------------------- 요청
    async def request(self, method: str, path: str, *, retry_idempotent: bool = True, **kw) -> httpx.Response:
        """상태 코드를 예외로 바꿔서 돌려준다.

        - 연결이 끊겨 있던 경우(서버가 먼저 닫음)는 GET 만 한 번 다시 보낸다.
          POST(주문 등)는 서버에 도착했는지 알 수 없으므로 다시 보내지 않는다 (보고서 4장).
        """
        attempts = 2 if (retry_idempotent and method.upper() == "GET") else 1
        for i in range(attempts):
            try:
                resp = await self._client.request(method, path, **kw)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                # 연결 자체가 안 됨: 요청이 서버에 가지 않았다
                if i == attempts - 1:
                    raise ServerError(f"{method} {path}: {type(e).__name__}", sent=False) from e
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError) as e:
                # 보내는 중이나 받는 중에 끊김: 서버가 받았을 수 있다
                if i == attempts - 1:
                    raise ServerError(f"{method} {path}: {type(e).__name__}") from e
            except httpx.TimeoutException as e:
                raise ServerError(f"{method} {path}: {type(e).__name__}") from e

        if resp.status_code == 401:
            raise AuthError(resp.headers.get("www-authenticate", "401"))
        if resp.status_code in (403, 429):
            raise BlockedError(f"{resp.status_code} {method} {path}: 차단 또는 요청 제한")
        if resp.status_code == 400:
            raise RequestError(resp.text[:300])
        if resp.status_code >= 500:
            raise ServerError(f"{resp.status_code} {resp.text[:200]}")
        resp.raise_for_status()   # 그 밖의 4xx (404 등)
        return resp
