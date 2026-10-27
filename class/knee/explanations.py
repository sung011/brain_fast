"""
사람이 쓰고 검수한 소견별 고정 설명 보관함.

이 파일에는 AI 가 만든 문장을 넣지 않는다. 의료 정보이므로 팀이 직접 쓰고 전문가가 검수한 문단만 넣는다(D-072).
항목을 추가하려면 아래 형식을 지키고, 검수한 사람·날짜를 reviewed_by 에 적는다.

    EXPLANATIONS["acl_v1"] = {
        "version": "1",
        "text": "(3~4문장)",
        "reviewed_by": "검수자 식별(예: 이름/역할, 날짜)",
    }

지금은 비어 있다. 설명이 없는 소견은 결과 카드로 공개하지 않는다(gates.find_gate 가 걸러낸다).
"""
from __future__ import annotations

EXPLANATIONS: dict[str, dict[str, str]] = {}
