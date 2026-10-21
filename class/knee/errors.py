"""무릎 분석에서 쓰는 오류 종류. 호출하는 쪽이 원인에 따라 응답 코드를 나눌 수 있게 한다."""


class KneeInputError(ValueError):
    """올린 영상이 규칙에 맞지 않을 때(모양·자료형·크기·NaN 등). 사용자 입력 오류로 다룬다."""


class KneeAnalysisError(RuntimeError):
    """입력은 맞았는데 계산 결과가 이상할 때(NaN, 0~1 밖 확률 등). 서버 내부 오류로 다룬다."""


class KneeModelNotReady(RuntimeError):
    """모델/가중치를 준비하지 못했을 때(내려받기 실패, 권한 없음 등). 내부 원인은 밖으로 알리지 않는다."""


class KneeModelLoadError(RuntimeError):
    """가중치 내려받기·적재 중 난 오류를 한 번 정리해서 내보내는 예외(내부 호출용).

    원래 예외의 문구에서 토큰을 가린 문장만 담고, 원래 예외는 cause/context 로 붙이지 않는다.
    (원 예외를 그대로 다시 던지면 문구·traceback·로그에 토큰이 남을 수 있다.) RuntimeError 이므로 기존 호출부의 처리는 그대로다.
    """
