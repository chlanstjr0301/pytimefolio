"""래퍼가 던지는 예외. 호출자는 TimefolioError 하나로 전부 잡을 수 있다."""


class TimefolioError(Exception):
    """모든 래퍼 예외의 부모."""


class AuthError(TimefolioError):
    """401: 토큰 갱신이나 재로그인으로 고쳐지는 오류."""


class RequestError(TimefolioError):
    """400: 요청을 만든 코드의 버그. 재시도해도 같은 결과라 재시도하지 않는다 (보고서 2.2)."""


class ServerError(TimefolioError):
    """5xx 또는 연결 실패: 서버나 네트워크 쪽 문제."""


class AmbiguousState(TimefolioError):
    """규칙이 하나로 정해지지 않을 때 (포트폴리오 후보가 0개나 2개 이상 등). 추측하지 않고 멈춘다 (목표 G5)."""
