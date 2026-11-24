"""rest: 조회용 REST 엔드포인트와 포트폴리오 선택 규칙.

근거 (보고서 2.3, 4장):
- 날짜(d)는 서버가 아무 날짜나 받아 주고 틀려도 오류를 내지 않는다 -> 기본값은 늘 currBizDay.
- 영업일은 06:02~07:07 사이에 넘어간다 -> currBizDay 가 KST 오늘 날짜와 다르면 주문하지 않는다.
- active 는 모든 포트폴리오에서 False -> ctstId, invalid(키가 없으면 False), lastD 로 고른다.
- Orders 는 d 가 아니라 d0/d1 기간을 받는다.
"""
from __future__ import annotations

import datetime as dt

from .auth import Auth
from .errors import AmbiguousState

KST = dt.timezone(dt.timedelta(hours=9))


class Rest:
    def __init__(self, auth: Auth):
        self.a = auth
        self.pf_id: int | None = None

    # ---------------------------------------------------------------- 날짜와 포트폴리오
    @property
    def biz_day(self) -> str:
        return self.a.biz_day

    def kst_today(self) -> str:
        return dt.datetime.fromtimestamp(self.a.t.server_time(), KST).strftime("%Y-%m-%d")

    def biz_day_is_today(self) -> bool:
        """주문 전 확인용: 서버 영업일이 오늘(KST, 서버 시계 기준)인가."""
        return self.biz_day == self.kst_today()

    def select_portfolio(self, ctst_id: int) -> int:
        """대회 ID 가 맞고, 리셋되지 않았고(invalid 없음), 종료일이 지나지 않은 포트폴리오. 정확히 하나여야 한다."""
        pfs = self.a.login_reply.get("pfs") or []
        cands = [p for p in pfs
                 if p.get("ctstId") == ctst_id and not p.get("invalid") and str(p.get("lastD", ""))[:10] >= self.biz_day]
        if len(cands) != 1:
            raise AmbiguousState(f"ctstId={ctst_id} 후보 {len(cands)}개: {[p.get('Id') for p in cands]}")
        self.pf_id = cands[0]["Id"]
        return self.pf_id

    def _pf(self) -> int:
        if self.pf_id is None:
            raise AmbiguousState("select_portfolio() 를 먼저 불러야 함")
        return self.pf_id

    async def _get(self, name: str, **params):
        r = await self.a.call("GET", f"/api/Portfolio/{name}", params=params)
        return r.json()

    # ---------------------------------------------------------------- 조회
    async def session(self, d: str | None = None) -> dict:
        """주문이 체결되는 시간대. {'hm0': '09:00', 'hm1': '15:20'}"""
        return await self._get("Session", d=d or self.biz_day)

    async def universe(self, d: str | None = None) -> list[dict]:
        """투자 가능 종목 [{Id, nm, sec}]. 종목코드는 문자열로 다룬다 (A0015M0 같은 코드가 있음)."""
        return await self._get("Universe", pfId=self._pf(), d=d or self.biz_day)

    async def trades(self, d: str | None = None) -> list[dict]:
        """진입부터 청산까지 포지션 단위 기록."""
        return await self._get("Trades", pfId=self._pf(), d=d or self.biz_day)

    async def violations(self, d: str | None = None) -> dict:
        return await self._get("Violations", pfId=self._pf(), d=d or self.biz_day)

    async def orders(self, d0: str | None = None, d1: str | None = None) -> list[dict]:
        """기간 안의 모든 주문 (체결 완료, 정지 포함)."""
        d0 = d0 or self.biz_day
        return await self._get("Orders", d0=d0, d1=d1 or d0, pfId=self._pf())

    async def validate_product(self, prod_id: str, entry: bool = True, d: str | None = None) -> list:
        """주문 전에 종목이 주문 가능한지 묻는다. 문제가 없으면 빈 warnings."""
        r = await self._get("ValidateProduct", d=d or self.biz_day, prodId=prod_id, entry=str(entry).lower())
        return r.get("warnings", [])
