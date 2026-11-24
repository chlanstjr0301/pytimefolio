"""state: 실시간 피드의 스냅샷과 증분을 적용해 포트폴리오 상태를 유지한다.

근거 (보고서 2.4, 장중 측정):
- 스냅샷: bs, ords, pos, msecs 가 채워져 온다 -> 통째로 교체.
- 증분: 그 넷은 null, 대신 *_diff = {added, removed, updated, ords_chgd}.
- updated 는 {"레코드 Id(문자열)": {바뀐 필드만}} -> int 로 바꿔서 필드 병합.
- 키는 종목코드가 아니라 레코드 Id -> prodId -> Id 색인을 따로 둔다.
- 서버가 arguments 에 null 을 보낼 때가 있다 -> 상태는 그대로 두고 다음에 스냅샷을 받는다.
"""
from __future__ import annotations

import copy


_KNOWN = {"t", "kstNow", "bs", "pos", "ords", "msecs", "pos_diff", "ords_diff", "msecs_diff"}


def _key(rec: dict):
    for k in ("Id", "id", "sec"):
        if k in rec:
            return rec[k]
    raise KeyError(f"레코드 키를 모름: {sorted(rec)[:6]}")


def _norm(k):
    return int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k


class PortfolioState:
    def __init__(self):
        self.bs: dict | None = None            # 잔고 요약
        self.pos: dict = {}                    # Id -> 보유 레코드
        self.ords: dict = {}                   # Id -> 주문 레코드
        self.msecs: dict = {}                  # 키 -> 시장 섹터 비중
        self.t: int | None = None              # 마지막 응답의 t (해석하지 않고 되돌려 보내기만 한다)
        self.kst_now: str | None = None
        self.extra: dict = {}                  # 그 밖의 최상위 필드 (cmpl: 회전율, tags: 소형주 표시 등)
        self.snapshots = 0
        self.diffs = 0

    # ---------------------------------------------------------------- 적용
    def apply(self, p: dict | None) -> str:
        """응답 하나를 적용하고 종류를 돌려준다: 'full', 'diff', 'null'."""
        if not p:
            return "null"
        self.t, self.kst_now = p.get("t", self.t), p.get("kstNow", self.kst_now)
        if p.get("pos") is not None:
            self.bs = p.get("bs")
            self.pos = {_key(r): r for r in p.get("pos") or []}
            self.ords = {_key(r): r for r in p.get("ords") or []}
            self.msecs = {_key(r): r for r in p.get("msecs") or []}
            self.extra = {k: v for k, v in p.items() if k not in _KNOWN}
            self.snapshots += 1
            return "full"
        if p.get("bs") is not None:
            self.bs = p["bs"]
        elif isinstance(p.get("bs_updated"), dict):     # 증분에서 잔고 변화가 오는 필드 (내용 확인 필요)
            self.bs = {**(self.bs or {}), **p["bs_updated"]}
        for name, store in (("pos_diff", self.pos), ("ords_diff", self.ords), ("msecs_diff", self.msecs)):
            self._apply_diff(store, p.get(name))
        for k, v in p.items():                 # 증분에 실려 온 그 밖의 필드는 null 이 아니면 덮어쓴다
            if k not in _KNOWN and not k.endswith("_diff") and v is not None:
                self.extra[k] = v
        self.diffs += 1
        return "diff"

    @staticmethod
    def _apply_diff(store: dict, d: dict | None) -> None:
        if not d:
            return
        for r in d.get("added") or []:
            store[_key(r)] = r
        for r in d.get("removed") or []:
            store.pop(_norm(_key(r)) if isinstance(r, dict) else _norm(r), None)
        for k, changed in (d.get("updated") or {}).items():
            k = _norm(k)
            if isinstance(changed, dict):
                store[k] = {**store.get(k, {}), **changed}

    # ---------------------------------------------------------------- 조회
    def by_prod(self) -> dict[str, dict]:
        """종목코드 -> 보유 레코드."""
        return {r["prodId"]: r for r in self.pos.values() if "prodId" in r}

    def weight(self, prod_id: str) -> float:
        r = self.by_prod().get(prod_id)
        return float(r.get("wei") or 0) if r else 0.0

    def open_orders(self) -> list[dict]:
        return [o for o in self.ords.values() if o.get("state") != "Done"]

    def snapshot_equal(self, other: "PortfolioState", fields=("pos", "ords")) -> list[str]:
        """증분 누적 상태와 새 스냅샷을 비교해 다른 곳을 돌려준다 (검증용)."""
        diffs = []
        for f in fields:
            a, b = getattr(self, f), getattr(other, f)
            for k in set(a) | set(b):
                if a.get(k) != b.get(k):
                    ka = {x for x in set(a.get(k, {})) | set(b.get(k, {})) if a.get(k, {}).get(x) != b.get(k, {}).get(x)}
                    diffs.append(f"{f}[{k}]: {sorted(ka)[:6]}")
        return diffs

    def copy(self) -> "PortfolioState":
        return copy.deepcopy(self)
