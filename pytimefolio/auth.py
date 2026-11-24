"""auth: 액세스 토큰을 늘 유효하게 유지한다.

근거 (보고서 2.2):
- 토큰 수명 900 s, 만료 후 300 s 허용 -> 허용치에 기대지 않고 발급 720 s 뒤 갱신.
- 갱신(SilentRefresh)은 로그인 때 받은 쿠키 전체가 있어야 한다. refreshToken 만으로는 401.
- 쿠키는 로그인 뒤 7일에 만료되고 갱신해도 늘어나지 않는다 -> 만료 1일 전에 다시 로그인.
- Logout 은 서버에서 아무것도 끊지 않는다 -> 쿠키 파일은 7일짜리 비밀번호. 0600 권한으로 저장소 밖에 둔다.
- 로그인할 때마다 7일짜리 세션이 늘어난다 -> 저장된 쿠키로 갱신을 먼저 시도하고, 실패할 때만 로그인.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path

from .errors import AuthError
from .transport import Transport

log = logging.getLogger("pytimefolio.auth")

DEFAULT_SESSION = Path.home() / ".config" / "pytimefolio" / "session.json"
REFRESH_AFTER = 720          # 발급 후 이 초가 지나면 갱신
RELOGIN_BEFORE = 24 * 3600   # 쿠키 만료 이 초 전에 재로그인


def jwt_claims(token: str) -> dict:
    """서명 검증 없이 JWT 본문(claims)만 읽는다. exp, iat 를 쓰려는 것."""
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


class Auth:
    def __init__(self, transport: Transport, email: str | None = None, password: str | None = None,
                 session_file: Path | str = DEFAULT_SESSION):
        self.t = transport
        self._email, self._password = email, password
        self.session_file = Path(session_file)
        self._token: str | None = None
        self._iat = 0.0
        self.biz_day: str | None = None          # 서버 영업일 (currBizDay)
        self.login_reply: dict = {}              # pfs, ctsts 등. 개인정보가 섞여 있으므로 로그에 남기지 않는다
        self._lock = asyncio.Lock()              # 갱신이 동시에 두 번 나가지 않게 (single-flight)

    # ---------------------------------------------------------------- 쿠키 저장/복원
    def _save_cookies(self) -> None:
        rows = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "expires": c.expires}
                for c in self.t.cookies.jar]
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.session_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rows, f)
        os.chmod(self.session_file, 0o600)       # 이미 있던 파일이면 권한을 다시 맞춘다

    def _load_cookies(self) -> bool:
        if not self.session_file.exists():
            return False
        for c in json.loads(self.session_file.read_text()):
            self.t.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
            if c["name"] == "refreshToken":
                self._refresh_expires = c["expires"]
        return True

    @property
    def refresh_expires(self) -> float | None:
        """refreshToken 쿠키의 만료 시각 (Unix 초)."""
        for c in self.t.cookies.jar:
            if c.name == "refreshToken" and c.expires:
                return float(c.expires)
        return getattr(self, "_refresh_expires", None)

    # ---------------------------------------------------------------- 서버 호출
    def _accept(self, reply: dict) -> None:
        self._token = reply["accessToken"]
        self._iat = float(jwt_claims(self._token)["iat"])
        self.biz_day = reply.get("currBizDay", self.biz_day)
        self.login_reply = reply

    async def login(self) -> None:
        if not (self._email and self._password):
            raise AuthError("저장된 세션으로 갱신할 수 없고, 로그인 정보도 없음")
        r = await self.t.request("POST", "/api/Auth/Login", json={"email": self._email, "password": self._password})
        self._accept(r.json())
        self._save_cookies()
        log.info("로그인 완료 (쿠키 만료 %s)", time.strftime("%m-%d %H:%M", time.localtime(self.refresh_expires or 0)))

    async def refresh(self) -> None:
        r = await self.t.request("POST", "/api/Auth/SilentRefresh")   # 쿠키는 클라이언트가 붙여 보낸다
        self._accept(r.json())
        log.info("토큰 갱신 (서버 영업일 %s)", self.biz_day)

    async def start(self) -> None:
        """저장된 쿠키로 갱신을 먼저 시도하고, 안 되면 로그인한다."""
        await self.t.warm_up()
        if self._load_cookies():
            try:
                await self.refresh()
                return
            except AuthError:
                log.info("저장된 세션이 만료됨 -> 로그인")
        await self.login()

    # ---------------------------------------------------------------- 토큰 제공
    def _needs_refresh(self) -> bool:
        return self._token is None or self.t.server_time() >= self._iat + REFRESH_AFTER

    async def token(self) -> str:
        """언제 불러도 유효한 토큰을 돌려준다. 필요하면 그 자리에서 갱신한다."""
        if self._needs_refresh():
            async with self._lock:
                if self._needs_refresh():        # 락을 기다리는 동안 다른 쪽이 이미 갱신했을 수 있다
                    await self._renew()
        return self._token

    async def force_refresh(self, stale: str | None) -> str:
        """요청이 401 을 받았을 때 부른다. 이미 다른 쪽이 갱신했으면 다시 하지 않는다."""
        async with self._lock:
            if stale is None or stale == self._token:
                await self._renew()
        return self._token

    async def _renew(self) -> None:
        exp = self.refresh_expires
        if exp and exp - self.t.server_time() < RELOGIN_BEFORE:
            log.info("쿠키 만료가 하루 안으로 남음 -> 재로그인")
            await self.login()
            return
        try:
            await self.refresh()
        except AuthError:
            await self.login()

    # ---------------------------------------------------------------- 인증된 요청
    async def call(self, method: str, path: str, **kw):
        """Bearer 를 붙여 보낸다. 401 이면 한 번만 갱신하고 다시 보낸다.
        401 은 서버가 요청을 처리하기 전에 거절한 것이므로 POST 라도 다시 보내도 중복이 생기지 않는다."""
        tok = await self.token()
        headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {tok}"}
        try:
            return await self.t.request(method, path, headers=headers, **kw)
        except AuthError:
            tok = await self.force_refresh(tok)
            headers["Authorization"] = f"Bearer {tok}"
            return await self.t.request(method, path, headers=headers, **kw)
