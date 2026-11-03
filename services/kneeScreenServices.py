"""무릎 화면 캡처 판독 문구를 review_node 에 남긴다.

이미지 바이트·오버레이 그림은 넣지 않는다. 소견 이름, 점수, 판정 문구만 저장한다.
학습 화면(POST /learning/roi-grade/knee)이 기준 ROI 없이 미채점이어도, 그 장의 모델 점수를 같은 표에 남긴다.
"""
from __future__ import annotations

import json
from importlib import import_module

import numpy as np

from repositories import reviewNodeRepositories as review_repo


def save_screen_reading(*, user_idx: int | None, result: dict) -> int:
    """판독 요약을 review_node 한 줄로 저장하고 그 번호를 돌려준다."""
    from db import SessionLocal

    findings = result.get("findings") or {}
    scored = [
        (name, item)
        for name, item in findings.items()
        if isinstance(item, dict) and item.get("status") == "evaluated" and item.get("prob") is not None
    ]
    top_name = max(scored, key=lambda pair: float(pair[1]["prob"]))[0] if scored else None
    disease = None
    if top_name is not None:
        disease = str((findings.get(top_name) or {}).get("label_ko") or "")[:30] or None

    summary_parts = []
    stored_findings = {}
    for name, item in findings.items():
        if not isinstance(item, dict):
            continue
        label = item.get("label_ko") or name
        if item.get("verdict"):
            summary_parts.append(f"{label}: {item.get('verdict')}")
        elif item.get("reason"):
            summary_parts.append(f"{label}: {item.get('reason')}")
        stored_findings[name] = {
            key: item.get(key)
            for key in (
                "label_ko", "status", "reason_code", "prob", "verdict",
                "confidence_band", "planes_used", "missing_planes",
            )
        }

    image = result.get("image") if isinstance(result.get("image"), dict) else {}
    payload = {
        "request_id": result.get("request_id"),
        "plane": image.get("plane"),
        "image_scope": image.get("image_scope"),
        "analysis_status": result.get("analysis_status"),
        "findings": stored_findings,
    }
    db = SessionLocal()
    try:
        row = review_repo.create(
            db,
            rn_u_idx=user_idx,
            rn_s_idx=None,
            rn_disease=disease,
            rn_image=None,
            rn_solution=None,
            rn_overlay_summary=" / ".join(summary_parts)[:2000] or None,
            rn_findings=json.dumps(payload, ensure_ascii=False),
        )
        return int(row.idx)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _corner_box(located: dict) -> dict:
    """ACL 위치 모델의 중심 좌표를 왼쪽 위 기준 박스로 바꾼다."""
    width = float(located["width"])
    height = float(located["height"])
    x = max(0.0, float(located["x_center"]) - width / 2)
    y = max(0.0, float(located["y_center"]) - height / 2)
    if x + width > 1:
        width = 1 - x
    if y + height > 1:
        height = 1 - y
    return {"x": round(x, 4), "y": round(y, 4), "width": round(width, 4), "height": round(height, 4)}


def _mask_from_box(shape: tuple[int, int], box: dict) -> np.ndarray:
    """0~1 박스를 사용자 ROI와 같은 격자 마스크로 옮긴다."""
    height, width = shape
    mask = np.zeros((height, width), dtype=bool)
    x1 = max(0, min(width - 1, int(np.floor(box["x"] * width))))
    y1 = max(0, min(height - 1, int(np.floor(box["y"] * height))))
    x2 = max(x1 + 1, min(width, int(np.ceil((box["x"] + box["width"]) * width))))
    y2 = max(y1 + 1, min(height, int(np.ceil((box["y"] + box["height"]) * height))))
    mask[y1:y2, x1:x2] = True
    return mask


def _grade_against_acl(gray: np.ndarray, user_mask: np.ndarray | None) -> tuple[dict, dict | None]:
    """뇌 CT와 같이, 모델이 찾은 영역과 그린 박스의 겹침으로 정답/부분정답/오답을 낸다."""
    from services import roiGradeServices as roi_grade

    located = None
    if user_mask is not None:
        try:
            # 기본 0.25에서는 이 영상처럼 박스가 버려져 그린 위치와 상관없이 전부 오답이 된다.
            # 학습 채점은 0.05 이상인 박스 중 가장 높은 것을 뇌 CT의 이상 부위처럼 기준으로 쓴다.
            located = import_module("class.knee.localization").Localization.locate_acl(
                gray.astype("float32") / 255.0, conf=0.05
            )
        except Exception:
            located = None
    if user_mask is None:
        return {"result": "오답", "iou": None, "recall": None}, None
    if not located:
        empty = np.zeros(user_mask.shape, dtype=bool)
        overlap = roi_grade.grade_overlap(user_mask.astype(bool), empty)
        return overlap, None
    box = _corner_box(located)
    overlap = roi_grade.grade_overlap(user_mask.astype(bool), _mask_from_box(user_mask.shape, box))
    return overlap, box


def _slice_number(raw: str | None) -> int | None:
    """화면이 보낸 슬라이스 위치. 비어 있으면 0장(첫 장). 숫자가 아니면 None."""
    if raw is None or str(raw).strip() == "":
        return 0
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    if value < 0:
        return None
    return value


async def read_problem_slice(db, *, study_idx: int, slice_index_raw: str | None, user_idx: int | None, user_mask: np.ndarray | None = None, plane: str = "sagittal") -> dict | None:
    """문제 영상 한 장을 모델로 읽고 review_node 에 남긴다. 실패하면 None(채점 응답은 그대로 둔다)."""
    from services import learningServices as learning_service

    index = _slice_number(slice_index_raw)
    if index is None or plane not in ("axial", "coronal", "sagittal"):
        return None
    from starlette.concurrency import run_in_threadpool

    try:
        content, _media, _name = await learning_service.fetch_problem_image(db, study_idx, index)
        screen = import_module("class.knee.screen_roi")
        imageio = import_module("class.knee.imageio")

        def _score():
            decoded = imageio.decode_screen_image(content)
            analyzed = screen.analyze(
                decoded.gray, plane=plane, image_scope="full_slice", include_overlay=False,
                width=decoded.width, height=decoded.height, request_id=f"study_{study_idx}_slice_{index}",
            )
            overlap, answer_box = _grade_against_acl(decoded.gray, user_mask)
            return analyzed, overlap, answer_box

        analyzed, overlap, answer_box = await run_in_threadpool(_score)
    except Exception:
        return None

    findings = []
    summary_parts = [f"{index + 1}번째 장({plane})"]
    top_label = None
    top_prob = -1.0
    for key, item in (analyzed.get("findings") or {}).items():
        if not isinstance(item, dict):
            continue
        row = {
            "key": key,
            "label_ko": item.get("label_ko"),
            "status": item.get("status"),
            "prob": item.get("prob"),
            "verdict": item.get("verdict"),
            "reason": item.get("reason"),
        }
        findings.append(row)
        if item.get("status") == "evaluated" and item.get("verdict"):
            summary_parts.append(f"{item.get('label_ko')}: {item.get('verdict')} ({item.get('prob')})")
            prob = float(item.get("prob") or 0)
            if prob >= top_prob:
                top_prob = prob
                top_label = item.get("label_ko")
        elif item.get("reason"):
            summary_parts.append(f"{item.get('label_ko')}: {item.get('reason')}")
    summary = ". ".join(summary_parts)
    public = {
        "plane": plane,
        "slice_index": index,
        "slice_number": index + 1,
        "analysis_status": analyzed.get("analysis_status"),
        "summary": summary,
        "disclaimer": "학습용 모델 점수이며 진단이 아닙니다.",
        "findings": findings,
    }
    from repositories import studyRepositories as study_repositories

    row = study_repositories.get_by_idx(db, study_idx)
    paths = learning_service.problem_image_paths(row) if row is not None else []
    image_path = paths[index] if 0 <= index < len(paths) else None
    try:
        saved = review_repo.create(
            db,
            rn_u_idx=user_idx,
            rn_s_idx=study_idx,
            rn_disease=(str(top_label)[:30] if top_label else None),
            rn_image=image_path,
            rn_solution=import_module("services.reviewNodeServices").solution_code(overlap["result"]),
            rn_recall=overlap.get("recall"),
            rn_iou=overlap.get("iou"),
            rn_overlay_summary=summary[:2000],
            rn_findings=json.dumps(public, ensure_ascii=False),
            rn_slice_index=index,
        )
    except Exception:
        db.rollback()
        return None
    return {
        "public": public,
        "summary": summary,
        "review_idx": int(saved.idx),
        "grade_result": overlap["result"],
        "iou": overlap.get("iou"),
        "recall": overlap.get("recall"),
        "reference_bbox": answer_box,
    }
