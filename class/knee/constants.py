"""무릎 MRI 분석에서 쓰는 고정 문구·이름."""

TASK_KO = {
    "abnormal": "이상 소견",
    "acl": "ACL(전방십자인대)",
    "meniscus": "반월판",
}

VERDICT = {
    "abnormal": ("이상 소견 가능성 높음", "이상 소견 가능성 낮음"),
    "acl": ("ACL 파열 가능성 높음", "ACL 파열 가능성 낮음"),
    "meniscus": ("반월판 파열 가능성 높음", "반월판 파열 가능성 낮음"),
}

UNCERTAIN_VERDICT = "판단 어려움"

ACL_REGION_LABEL = "acl_region"

DISCLAIMER = (
    "이 결과는 학습용 참고 자료이며 의료 진단이 아닙니다. "
    "박스는 ACL 이 있는 대략적 해부학 위치이고, 히트맵은 모델이 참고한 영역일 뿐 병변 위치가 아닙니다."
)
