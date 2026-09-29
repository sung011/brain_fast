"""무릎 분석에서 쓰는 오류 종류. 호출하는 쪽이 원인에 따라 응답 코드를 나눌 수 있게 한다."""


class KneeInputError(ValueError):
    """올린 영상이 규칙에 맞지 않을 때(모양·자료형·크기·NaN 등). 사용자 입력 오류로 다룬다."""


class KneeAnalysisError(RuntimeError):
    """입력은 맞았는데 계산 결과가 이상할 때(NaN, 0~1 밖 확률 등). 서버 내부 오류로 다룬다."""
