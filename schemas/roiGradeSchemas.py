"""ROI 채점 입력값. 흉부 X-ray 는 학습자가 그린 ROI 여러 개(rois, JSON 배열)를 받는다."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

# 의심 부위마다 하나씩 그려도 이 정도면 충분하다. 너무 많이 보내 전체를 덮는 것을 막는다
MAX_ROIS = 10


class RoiShape(BaseModel):
    """ROI 한 개. 좌표는 이미지 대비 0~1 비율."""

    model_config = ConfigDict(extra="forbid")

    roi_type: Literal["box", "circle"]
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    cx: float | None = None
    cy: float | None = None
    radius: float | None = None

    @model_validator(mode="after")
    def check_fields(self):
        if self.roi_type == "box":
            if None in (self.x, self.y, self.width, self.height):
                raise ValueError("박스는 x, y, width, height 가 필요합니다.")
        elif None in (self.cx, self.cy, self.radius):
            raise ValueError("원은 cx, cy, radius 가 필요합니다.")
        return self


def parse_rois(raw: str | None) -> list[dict] | None:
    """
    rois 폼 값(JSON 문자열) → ROI 목록. 값이 없으면 None (예전처럼 ROI 하나만 채점).

    형식이 틀리면 ValueError (라우터가 400 으로 돌려준다).
    """
    if raw is None or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("rois 는 JSON 배열이어야 합니다.") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("rois 는 ROI 를 하나 이상 담은 배열이어야 합니다.")
    if len(data) > MAX_ROIS:
        raise ValueError(f"ROI 는 최대 {MAX_ROIS}개까지 제출할 수 있습니다.")
    try:
        shapes = [RoiShape.model_validate(item) for item in data]
    except ValidationError as exc:
        raise ValueError(
            "rois 의 각 항목은 box(x, y, width, height) 또는 circle(cx, cy, radius) 이어야 합니다."
        ) from exc
    return [shape.model_dump() for shape in shapes]
