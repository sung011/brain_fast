"""
부위 자동 판별 API 라우터.

main.py 에서 prefix="/api/v1/body-part" 를 붙인다.
실제 주소 예: /api/v1/body-part/health, /api/v1/body-part/check
판별 로직은 class/bodypart, 업무 규칙은 services/bodyPartServices.

하는 일:
- GET  /api/v1/body-part/health : 판별 모델 준비 상태
- POST /api/v1/body-part/check  : 사진이 어떤 부위인지 맞히고, 고른 부위와 다르면 경고 문구를 돌려줌
"""
from importlib import import_module

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from schemas.bodyPartSchemas import BodyPartHealth, BodyPartResponse
from services import bodyPartServices

router = APIRouter()


@router.get("/health", response_model=BodyPartHealth)
async def health() -> BodyPartHealth:
    return BodyPartHealth(**bodyPartServices.health())


@router.post("/check", response_model=BodyPartResponse)
async def check(image: UploadFile = File(...), selected: str = Form("")) -> BodyPartResponse:
    config = import_module("class.bodypart.config")
    data = await image.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다 (최대 5MB).")
    if len(data) < 64:
        raise HTTPException(status_code=400, detail="유효한 이미지가 아닙니다.")
    try:
        return BodyPartResponse(**bodyPartServices.check(data, selected))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
