"""래퍼가 던지는 예외. 호출자는 TimefolioError 하나로 전부 잡을 수 있다."""


class TimefolioError(Exception):
    """모든 래퍼 예외의 부모."""


class AuthError(TimefolioError):
    """401: 토큰 갱신이나 재로그인으로 고쳐지는 오류."""


class RequestError(TimefolioError):
    """400: 요청을 만든 코드의 버그. 재시도해도 같은 결과라 재시도하지 않는다 (보고서 2.2)."""


class ServerError(TimefolioError):
    """5xx 또는 연결 실패: 서버나 네트워크 쪽 문제.
    sent=False 는 요청이 서버에 닿지 않았음이 확실한 경우 (연결 실패, 연결 대기 시간 초과)."""

    def __init__(self, msg: str, sent: bool = True):
        super().__init__(msg)
        self.sent = sent


class BlockedError(TimefolioError):
    """403 또는 429: 운영사가 차단했거나 요청을 제한함. 래퍼는 이후 모든 쓰기를 멈춘다."""


class StaleFeed(TimefolioError):
    """실시간 피드가 멈췄거나 마지막 응답이 너무 오래됨. 오래된 상태로 주문하지 않으려고 멈춘다."""


class RebalanceError(TimefolioError):
    """rebalance() 도중 실패. placed 에 이미 넣은 주문 ID 가 들어 있다."""

    def __init__(self, msg: str, placed: list[int]):
        super().__init__(f"{msg} (이미 넣은 주문: {placed})")
        self.placed = placed


class AmbiguousState(TimefolioError):
    """규칙이 하나로 정해지지 않을 때 (포트폴리오 후보가 0개나 2개 이상 등). 추측하지 않고 멈춘다 (목표 G5)."""
