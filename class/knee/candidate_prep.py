"""
무릎 학습 "대표 영상 후보"와 팀 검수 자료를 만드는 **서버 밖 도구**(오프라인, 사람이 돌린다). 후보만 만들고 승인하지 않는다.

이 파일은 서버 코드(routers·services·main)가 불러오지 않는다(정적 시험으로 확인). 로컬에 있는 KneeMRI 볼륨(pickle)을 읽어
후보 PNG·검수용 HTML·후보 목록(JSON)을 만들고, 검수자 3명의 결과 파일을 모아 **일치 여부만** 보고한다.
승인표(`APPROVED_SLIDE_REFERENCES`)를 바꾸거나 DB·NAS 에 쓰거나 기능을 켜지 않는다. 실제 등록은 사용자·Codex 별도 승인 뒤 사람이 한다.

데이터 형식(데이터셋 `example.py` 로 확인) — 변환을 모두 이름 붙여 명시한다
- 볼륨 배열 축: `[z, y, x]`, 0 기반 번호. 한 장은 `volume[z]` 이고 행이 y(위→아래), 열이 x(왼쪽→오른쪽)다.
- 박스: `roiX, roiY, roiWidth, roiHeight` 는 **같은 한 장의 픽셀** 좌표(왼쪽 위 기준)이고, z 범위는 `roiZ` 부터 `roiDepth` 장이다(`roiZ` 는 범위의 시작이지 대표 번호가 아니다).
  메타데이터 열 순서는 `roiHeight, roiWidth` 이므로 이름으로 읽는다.
- PNG 변환: **전치·회전·뒤집기 없음**(`transpose=False, flip_x=False, flip_y=False, rotate=0`). 12비트 값을 볼륨 전체의 백분위(기본 0.5~99.5)
  창으로 선형 변환해 8비트로 만든다(장마다 달라지지 않게 볼륨 하나에 창 하나). PNG 크기 = 원본 한 장의 크기(`W×H`)이고, 박스는 같은 크기로 나눈 값이다.
- 대표 후보 z = `roiZ + roiDepth // 2`(짝수 깊이는 뒤쪽 가운데). 가운데는 후보 선택일 뿐 가장 잘 보이는 장이나 병변 정답의 보장이 아니다.

공유해도 되는 것과 아닌 것
- 공유 가능: 사례 코드(`case-001`), PNG, 검수 시트(HTML), `candidate.json`(출처·라이선스·변환 사실·해시·치수·정규화 박스).
- **공유 금지**: `PRIVATE_provenance.json`(원본 검사 번호·볼륨 파일명·진단 라벨·원본 박스). 환자 식별에 쓰일 수 있는 연결키이므로 **기본으로는 만들지 않는다.**
  필요하면 `--private-dir`(공유 출력 폴더의 **바깥**, 접근을 제한한 곳)을 명시했을 때만 그곳에 만든다(안쪽·같은 폴더·상위 폴더는 거절).

보안: 원본 pickle 은 **신뢰하는 로컬 자료만** 읽는다(`trusted_local=True` 를 명시해야 하며, numpy 배열만 허용하는 제한된 역직렬화를 쓴다).
서버에 올라온 파일을 이 도구로 읽지 않는다.

사용(저장소 루트에서; 패키지 이름이 `class` 라 import 문을 쓸 수 없어 importlib 로 부른다)
  python -c "import importlib,sys; sys.exit(importlib.import_module('class.knee.candidate_prep').main(sys.argv[1:]))" prepare \
      --metadata <metadata.csv> --volumes <volumetric_data 폴더> --volume-file <파일.pck> [--volume-file ...] --out <새 폴더> --trusted-local
  python -c "..." summarize --out <위 폴더> --reviews <검수 결과 JSON 들이 있는 폴더>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import pickle
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .slice_manifest import build_manifest
from .slide_references import candidate_from_dataset_roi

SCHEMA = "knee-candidate-v1"
REVIEW_SCHEMA = "knee-review-v1"
CASE_CODE = re.compile(r"case-\d{3}")
REVIEWER_CODE = re.compile(r"reviewer-\d{3}")
VOLUME_FILE = re.compile(r"[A-Za-z0-9_-]{1,64}\.pck")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_SIDE = 4096
MAX_SLICES_IN_VOLUME = 512
DEFAULT_SLICES = 24
DEFAULT_WINDOW = (0.5, 99.5)
REQUIRED_REVIEWERS = 3

ORIENTATION = {
    "axes": "z,y,x", "index_base": 0, "transpose": False, "flip_x": False, "flip_y": False, "rotate_deg": 0,
    "box_origin": "top_left_of_the_same_slice", "png_size_equals_source_slice": True,
}
DATASET = {
    "name": "KneeMRI", "record": "https://zenodo.org/records/14789903",
    "source": "Clinical Hospital Centre Rijeka, Croatia",
    "paper": "I. Štajduhar, M. Mamula, D. Miletić, G. Unal, Semi-automated detection of anterior cruciate ligament injury from MRI, "
             "Computer Methods and Programs in Biomedicine 140 (2017) 151-164, https://doi.org/10.1016/j.cmpb.2016.12.006",
    "license": "CC BY-NC-ND 4.0",
    "transform_note": "원본 볼륨의 한 단면을 8비트 PNG 로 변환했다(전치·회전·뒤집기 없음, 볼륨 전체 백분위 창 선형 변환).",
}
PLANE = "sagittal"
PLANE_EVIDENCE = "KneeMRI 는 시상면 PD-FS 영상으로 알려져 있다(문헌·학습 기록). 검수자가 시상면인지 직접 확인한다."
PURPOSE = "ACL이 포함된 참고 영역 표시 연습 · 데이터셋 라벨 기준, 임상 검증 아님"
CHECKLIST = (
    ("opens", "영상이 정상적으로 열린다(깨지거나 비어 있지 않다)"),
    ("orientation", "원본과 방향·비율이 같다(뒤집히거나 돌아가지 않았다)"),
    ("box_inside", "박스가 영상 안에 완전히 들어온다"),
    ("acl_region", "박스가 ACL이 포함된 대략적인 해부학 영역으로 보인다(확신이 없으면 체크하지 않는다)"),
)


class CandidateError(ValueError):
    """후보를 만들 수 없는 입력(범위 오류·형식 오류 등). 값을 고쳐 쓰지 않고 거절한다."""


# ── 메타데이터 ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MetaRow:
    exam_id: int
    series_no: int
    acl_diagnosis: int
    knee_lr: int
    roi_x: int
    roi_y: int
    roi_z: int
    roi_height: int
    roi_width: int
    roi_depth: int
    volume_filename: str


_COLUMNS = {
    "examId": "exam_id", "seriesNo": "series_no", "aclDiagnosis": "acl_diagnosis", "kneeLR": "knee_lr", "roiX": "roi_x", "roiY": "roi_y",
    "roiZ": "roi_z", "roiHeight": "roi_height", "roiWidth": "roi_width", "roiDepth": "roi_depth", "volumeFilename": "volume_filename",
}


def parse_metadata(text: str) -> list[MetaRow]:
    """메타데이터 CSV 를 **열 이름으로** 읽는다(열 순서에 의존하지 않는다). 정수가 아니거나 파일명이 안전하지 않으면 거절."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [name for name in _COLUMNS if name not in (reader.fieldnames or [])]
    if missing:
        raise CandidateError(f"메타데이터에 필요한 열이 없습니다: {', '.join(missing)}")
    rows = []
    for number, raw in enumerate(reader, start=2):
        values = {}
        for column, field in _COLUMNS.items():
            cell = (raw.get(column) or "").strip()
            if field == "volume_filename":
                if not VOLUME_FILE.fullmatch(cell):
                    raise CandidateError(f"{number}행: 볼륨 파일 이름이 안전하지 않습니다.")
                values[field] = cell
            else:
                if not re.fullmatch(r"-?\d{1,9}", cell):
                    raise CandidateError(f"{number}행: {column} 가 정수가 아닙니다.")
                values[field] = int(cell)
        rows.append(MetaRow(**values))
    return rows


# ── 볼륨 읽기(신뢰한 로컬 자료만) ───────────────────────────────────────────────────────
_ALLOWED_PICKLE = {
    ("numpy", "ndarray"), ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
    ("_codecs", "encode"),   # 옛 파이썬(프로토콜 2)에서 만든 배열 바이트를 복원할 때 쓴다(문자열→바이트 변환일 뿐 코드를 실행하지 않는다)
}


class _ArrayOnlyUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in _ALLOWED_PICKLE:
            return super().find_class(module, name)
        raise pickle.UnpicklingError("허용되지 않은 객체입니다(numpy 배열만 읽습니다).")


def load_volume_trusted(path, *, trusted_local: bool = False) -> np.ndarray:
    """볼륨 pickle 을 읽는다. **신뢰하는 로컬 자료**일 때만(trusted_local=True) 읽고, numpy 배열 외 객체는 거절한다."""
    if trusted_local is not True:
        raise CandidateError("원본 pickle 은 신뢰하는 로컬 자료만 읽습니다(trusted_local=True 를 명시해야 합니다).")
    with open(path, "rb") as handle:
        volume = _ArrayOnlyUnpickler(handle).load()
    return check_volume(volume)


def check_volume(volume) -> np.ndarray:
    if not isinstance(volume, np.ndarray) or volume.ndim != 3:
        raise CandidateError("볼륨은 3차원 numpy 배열[z, y, x]이어야 합니다.")
    z, y, x = volume.shape
    if not (0 < z <= MAX_SLICES_IN_VOLUME and 0 < y <= MAX_SIDE and 0 < x <= MAX_SIDE):
        raise CandidateError("볼륨 크기가 범위를 벗어났습니다.")
    if volume.dtype.kind not in "iuf":
        raise CandidateError("볼륨 값은 숫자여야 합니다.")
    if volume.dtype.kind == "f" and not np.isfinite(volume).all():
        raise CandidateError("볼륨에 NaN/Infinity 가 있습니다.")
    return volume


# ── 영상 변환(전치·회전·뒤집기 없음) ──────────────────────────────────────────────────────
def volume_window(volume: np.ndarray, low_pct: float = DEFAULT_WINDOW[0], high_pct: float = DEFAULT_WINDOW[1]) -> tuple[float, float]:
    """볼륨 전체에서 창(lo, hi)을 정한다. 모든 장에 같은 창을 쓴다. 퇴화(hi<=lo)하면 최소~최대로, 그래도 같으면 (lo, lo+1)."""
    if not (0.0 <= low_pct < high_pct <= 100.0):
        raise CandidateError("백분위 창이 올바르지 않습니다.")
    lo, hi = (float(v) for v in np.percentile(volume, [low_pct, high_pct]))
    if not hi > lo:
        lo, hi = float(volume.min()), float(volume.max())
    if not hi > lo:
        hi = lo + 1.0
    return lo, hi


def to_uint8(slice2d: np.ndarray, lo: float, hi: float) -> np.ndarray:
    scaled = (slice2d.astype(np.float64) - lo) / (hi - lo)
    return np.clip(np.rint(scaled * 255.0), 0, 255).astype(np.uint8)


def png_bytes(gray: np.ndarray) -> bytes:
    from PIL import Image   # 서버 코드가 아니므로 필요할 때만 불러온다
    buffer = io.BytesIO()
    Image.fromarray(gray, "L").save(buffer, "PNG")
    return buffer.getvalue()


def choose_slices(candidate_z: int, total: int, count: int = DEFAULT_SLICES) -> list[int]:
    """대표 후보를 포함하는 연속된 원본 z 번호 목록(가능하면 가운데). 볼륨보다 많이 달라고 하면 전부."""
    if count <= 0 or total <= 0 or not 0 <= candidate_z < total:
        raise CandidateError("슬라이스 선택 값이 올바르지 않습니다.")
    count = min(count, total)
    start = candidate_z - count // 2
    start = max(0, min(start, total - count))
    return list(range(start, start + count))


# ── 사례(후보) 만들기 ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Case:
    case_code: str
    slices: tuple          # ({"index","source_z","png","sha256","width","height"}, ...) — 등록 배열 순서
    candidate: dict        # 후보 값(대표 번호·원본 z·정규화 박스·치수). 승인이 아니다
    window: dict
    private: dict          # 공유 금지 연결 정보


def build_case(volume: np.ndarray, row: MetaRow, case_code: str, *, count: int = DEFAULT_SLICES,
               window_pct: tuple[float, float] = DEFAULT_WINDOW) -> Case:
    """볼륨 한 개와 ROI 메타데이터 한 행에서 후보를 만든다. 범위를 자르거나 고쳐 쓰지 않고, 잘못되면 거절한다."""
    if not CASE_CODE.fullmatch(case_code):
        raise CandidateError("사례 코드 형식이 올바르지 않습니다(case-001).")
    check_volume(volume)
    total, height, width = volume.shape
    from .slice_manifest import SliceManifestError, candidate_source_z
    try:
        candidate_z = candidate_source_z(row.roi_z, row.roi_depth, total)
        zs = choose_slices(candidate_z, total, count)
        paths = [f"/candidate/{i:03d}.png" for i in range(len(zs))]
        manifest = build_manifest(json.dumps(paths), ("/candidate/",))   # 번호·범위 검증용 임시 목록(실제 경로가 아니다)
        raw = candidate_from_dataset_roi(
            manifest=manifest, source_numbers=zs, roi_x=row.roi_x, roi_y=row.roi_y, roi_z=row.roi_z,
            roi_width=row.roi_width, roi_height=row.roi_height, roi_depth=row.roi_depth,
            volume_slices=total, image_width=width, image_height=height,
        )
    except SliceManifestError as exc:
        raise CandidateError(str(exc)) from None

    lo, hi = volume_window(volume, *window_pct)
    slices = []
    for index, z in enumerate(zs):
        data = png_bytes(to_uint8(volume[z], lo, hi))
        slices.append({"index": index, "source_z": int(z), "png": data, "sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height})
    rep = slices[raw["slice_no"]]
    candidate = {
        "representative_slice_no": raw["slice_no"], "representative_source_z": raw["source_slice_no"], "selection": "roiZ + roiDepth // 2",
        "box": {"x": raw["x"], "y": raw["y"], "width": raw["width"], "height": raw["height"], "coord_system": "normalized_image_top_left"},
        "box_pixels": {"x": row.roi_x, "y": row.roi_y, "width": row.roi_width, "height": row.roi_height},
        "image_width": width, "image_height": height, "image_sha256": rep["sha256"],
    }
    window = {"low_percentile": window_pct[0], "high_percentile": window_pct[1], "lo": lo, "hi": hi, "scope": "whole_volume"}
    private = {
        "case_code": case_code, "exam_id": row.exam_id, "series_no": row.series_no, "volume_filename": row.volume_filename,
        "acl_diagnosis_label": row.acl_diagnosis, "knee_lr": row.knee_lr,
        "roi": {"x": row.roi_x, "y": row.roi_y, "z": row.roi_z, "width": row.roi_width, "height": row.roi_height, "depth": row.roi_depth},
    }
    return Case(case_code=case_code, slices=tuple(slices), candidate=candidate, window=window, private=private)


def fingerprint(case: Case) -> str:
    """검수 결과가 이 후보(대표 영상 내용 + 박스 + 대표 번호)에 묶이도록 하는 값."""
    c = case.candidate
    material = json.dumps([c["image_sha256"], c["box"], c["representative_slice_no"], [s["sha256"] for s in case.slices]], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def shareable(case: Case) -> dict:
    """공유해도 되는 후보 정보(환자 연결키·진단 라벨·원본 파일명 없음)."""
    return {
        "schema": SCHEMA, "case_code": case.case_code, "plane": PLANE, "plane_evidence": PLANE_EVIDENCE, "purpose": PURPOSE,
        "dataset": DATASET, "orientation": ORIENTATION, "window": case.window, "candidate": case.candidate,
        "slices": [{"index": s["index"], "source_z": s["source_z"], "sha256": s["sha256"], "width": s["width"], "height": s["height"],
                    "file": f"{case.case_code}/slice_{s['index']:03d}.png"} for s in case.slices],
        "fingerprint": fingerprint(case),
        "status": "CANDIDATE_ONLY_NOT_APPROVED",
    }


def _check_private_dir(out: Path, private_dir) -> Path:
    """비공개 연결 정보 폴더는 **공유 출력 폴더의 바깥**이어야 한다(안쪽·같은 폴더·상위 폴더 모두 거절)."""
    private = Path(private_dir).resolve()
    shared = out.resolve()
    if private == shared or shared in private.parents or private in shared.parents:
        raise CandidateError("비공개 폴더는 공유 출력 폴더의 바깥이어야 합니다(안쪽·같은 폴더·상위 폴더는 안 됩니다).")
    if private.exists() and any(private.iterdir()):
        raise CandidateError("비공개 폴더가 비어 있지 않습니다(덮어쓰지 않습니다).")
    return private


def write_cases(cases: list[Case], out_dir, *, private_dir=None) -> dict:
    """후보 PNG·candidate.json·검수 시트를 **공유 출력 폴더**에 쓴다. 이미 있는 파일은 덮어쓰지 않는다.

    환자 연결 정보(`PRIVATE_provenance.json`: 원본 검사 번호·볼륨 파일명·진단 라벨)는 **기본으로 만들지 않는다.** 필요할 때만
    `private_dir`(공유 출력 폴더 바깥, 접근을 제한한 곳)을 명시하면 거기에만 쓴다. 폴더 이름의 경고만으로는 분리가 보장되지 않기 때문이다.
    """
    out = Path(out_dir)
    private = _check_private_dir(out, private_dir) if private_dir is not None else None
    if out.exists() and any(out.iterdir()):
        raise CandidateError("출력 폴더가 비어 있지 않습니다(덮어쓰지 않습니다).")
    out.mkdir(parents=True, exist_ok=True)
    for case in cases:
        folder = out / case.case_code
        folder.mkdir()
        for s in case.slices:
            (folder / f"slice_{s['index']:03d}.png").write_bytes(s["png"])
        (folder / "candidate.json").write_text(json.dumps(shareable(case), ensure_ascii=False, indent=2), encoding="utf-8")
    sheet = out / "review_sheet.html"
    sheet.write_text(render_review_sheet(cases), encoding="utf-8")
    (out / "README.txt").write_text(
        "이 폴더는 후보와 검수 자료입니다. 승인된 것이 아닙니다(status=CANDIDATE_ONLY_NOT_APPROVED).\n"
        "review_sheet.html 을 브라우저로 열어 검수자 3명이 각자 확인하고 결과 파일(JSON)을 저장합니다.\n"
        "이 폴더에는 환자 연결 정보(원본 검사 번호·볼륨 파일명·진단 라벨)가 없습니다. 실제 등록은 별도 승인 뒤 사람이 합니다.\n", encoding="utf-8")
    private_file = None
    if private is not None:
        private.mkdir(parents=True, exist_ok=True)
        private_file = private / "PRIVATE_provenance.json"
        private_file.write_text(json.dumps({
            "WARNING": "공유 금지: 원본 검사 번호·볼륨 파일명·진단 라벨 등 환자 연결키가 들어 있습니다. 접근을 제한한 곳에만 보관하고 프로젝트 종료 후 폐기합니다.",
            "cases": [c.private for c in cases],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"out": str(out), "cases": [c.case_code for c in cases], "sheet": str(sheet),
            "private": str(private_file) if private_file is not None else None}


# ── 검수 시트(오프라인 HTML) ────────────────────────────────────────────────────────────
def render_review_sheet(cases: list[Case]) -> str:
    """검수자가 브라우저로 여는 단일 HTML. 외부 주소 없음. 각자 체크 후 결과를 JSON 파일로 저장한다."""
    esc = html.escape
    blocks = []
    for case in cases:
        rep = case.candidate["representative_slice_no"]
        box = case.candidate["box"]
        thumbs = "".join(
            f'<figure class="{ "rep" if s["index"] == rep else "" }"><img src="{esc(case.case_code)}/slice_{s["index"]:03d}.png" alt="슬라이스 {s["index"] + 1}">'
            f'<figcaption>{s["index"] + 1}</figcaption></figure>' for s in case.slices)
        checks = "".join(f'<label><input type="checkbox" data-check="{key}"> {esc(label)}</label>' for key, label in CHECKLIST)
        blocks.append(f"""
<section class="case" data-case="{esc(case.case_code)}" data-fingerprint="{fingerprint(case)}">
  <h2>{esc(case.case_code)} · 대표 {rep + 1}번째 / {len(case.slices)}장</h2>
  <div class="stage"><img class="rep" src="{esc(case.case_code)}/slice_{rep:03d}.png" alt="대표 영상 후보">
    <svg viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true"><rect x="{box['x']}" y="{box['y']}" width="{box['width']}" height="{box['height']}"/></svg></div>
  <p class="small">빨간 사각형 = 데이터셋의 ROI 박스(넓은 직사각형). 병변의 정확한 경계가 아니라 ACL이 포함된 대략적인 영역을 가리킵니다.</p>
  <div class="strip">{thumbs}</div>
  <fieldset><legend>검수 체크리스트(확신이 없으면 체크하지 말고 '승인하지 않음')</legend>{checks}
    <label>메모 <input type="text" data-note maxlength="200" placeholder="개인정보·실경로는 적지 마세요"></label></fieldset>
  <div class="actions"><button type="button" data-decide="approve">승인 의견으로 저장</button> <button type="button" data-decide="reject">승인하지 않음으로 저장</button></div>
</section>""")
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>무릎 대표 영상 후보 검수 시트</title>
<style>
body{{font-family:'Malgun Gothic',sans-serif;background:#0b100e;color:#e8f1ec;margin:0;padding:16px;line-height:1.6}}
h1{{font-size:20px}} h2{{font-size:16px}} .small{{color:#a5b9af;font-size:12px}} .notice{{border:1px solid #5a4a1f;background:#231d0a;padding:10px 14px;border-radius:8px;margin:8px 0 16px}}
.case{{border:1px solid #2e4038;border-radius:12px;padding:12px 16px;margin:16px 0}} .stage{{position:relative;display:inline-block;max-width:100%}}
.stage img.rep{{display:block;max-width:100%;height:auto;image-rendering:auto}} .stage svg{{position:absolute;inset:0;width:100%;height:100%}}
.stage rect{{fill:rgba(255,64,64,.12);stroke:#ff4040;stroke-width:2;vector-effect:non-scaling-stroke}}
.strip{{display:flex;gap:6px;overflow-x:auto;padding:6px 0}} .strip figure{{margin:0;text-align:center;font-size:11px;opacity:.7}} .strip figure.rep{{opacity:1;outline:2px solid #63edb1}}
.strip img{{height:72px;display:block}} fieldset{{border:1px solid #2e4038;border-radius:8px;margin:10px 0}} fieldset label{{display:block;margin:4px 0}}
select,input[type=text],button{{font:inherit;background:#111916;color:#e8f1ec;border:1px solid #4b6457;border-radius:6px;padding:6px 10px}} button{{cursor:pointer}}
</style></head><body>
<h1>무릎 대표 영상 후보 검수</h1>
<div class="notice"><strong>{esc(PURPOSE)}</strong><br>
이 자료는 <strong>후보</strong>이며 승인되지 않았습니다. 검수자는 임상 적합성·병변 정답·최적 가시성을 인증하지 않습니다. 확신할 수 없으면 승인하지 않습니다.<br>
영상 출처: {esc(DATASET['source'])} · {esc(DATASET['name'])} ({esc(DATASET['license'])}) · {esc(DATASET['record'])}<br>
논문: {esc(DATASET['paper'])}<br>{esc(DATASET['transform_note'])}</div>
<p>검수자 코드: <select id="reviewer">{''.join(f'<option>reviewer-{i:03d}</option>' for i in range(1, 10))}</select>
<span class="small">(실명·이메일을 쓰지 않는 가명 코드. 대응표는 팀장만 보관)</span></p>
{''.join(blocks)}
<script>
(function(){{
  function save(section, decision){{
    var checks = {{}}; section.querySelectorAll('[data-check]').forEach(function(c){{ checks[c.dataset.check] = c.checked; }});
    var all = Object.keys(checks).every(function(k){{ return checks[k]; }});
    if (decision === 'approve' && !all){{ alert('승인 의견은 모든 항목을 체크한 경우에만 저장할 수 있습니다.'); return; }}
    var data = {{schema: '{REVIEW_SCHEMA}', reviewer: document.getElementById('reviewer').value, case_code: section.dataset.case,
      fingerprint: section.dataset.fingerprint, decision: decision, checks: checks, note: (section.querySelector('[data-note]').value || '').slice(0, 200)}};
    var blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'review_' + data.reviewer + '_' + data.case_code + '.json';
    document.body.appendChild(a); a.click(); a.remove();
  }}
  document.querySelectorAll('.case').forEach(function(section){{
    section.querySelectorAll('[data-decide]').forEach(function(button){{ button.addEventListener('click', function(){{ save(section, button.dataset.decide); }}); }});
  }});
}})();
</script></body></html>
"""


# ── 검수 결과 모으기(일치 여부만 보고한다. 승인 항목을 만들지 않는다) ─────────────────────────
def summarize_reviews(candidate_files: list[dict], review_files: list[dict], *, required: int = REQUIRED_REVIEWERS) -> dict:
    """사례별 검수 일치 여부를 보고한다. 모두 승인이어도 '승인 준비됨'일 뿐 승인표를 바꾸거나 승인 항목을 만들지 않는다."""
    cases = {c["case_code"]: c for c in candidate_files}
    per_case = {code: {} for code in cases}
    errors = []
    for number, review in enumerate(review_files, start=1):
        if not isinstance(review, dict) or review.get("schema") != REVIEW_SCHEMA:
            errors.append(f"검수 파일 {number}: 형식이 올바르지 않습니다.")
            continue
        code, reviewer, decision = review.get("case_code"), review.get("reviewer"), review.get("decision")
        if code not in cases:
            errors.append(f"검수 파일 {number}: 알 수 없는 사례 코드입니다.")
            continue
        if not isinstance(reviewer, str) or not REVIEWER_CODE.fullmatch(reviewer):
            errors.append(f"검수 파일 {number}: 검수자 코드가 가명 코드(reviewer-000) 형식이 아닙니다.")
            continue
        if review.get("fingerprint") != cases[code]["fingerprint"]:
            errors.append(f"검수 파일 {number}: 이 후보와 다른 내용에 대한 검수입니다(지문 불일치).")
            continue
        checks = review.get("checks")
        if decision not in ("approve", "reject") or not isinstance(checks, dict) or set(checks) != {k for k, _ in CHECKLIST} \
                or not all(isinstance(v, bool) for v in checks.values()):
            errors.append(f"검수 파일 {number}: 판단 또는 체크리스트 값이 올바르지 않습니다.")
            continue
        if decision == "approve" and not all(checks.values()):
            errors.append(f"검수 파일 {number}: 승인 의견인데 체크하지 않은 항목이 있습니다.")
            continue
        if reviewer in per_case[code]:
            errors.append(f"검수 파일 {number}: 같은 검수자의 같은 사례 결과가 중복되었습니다.")
            continue
        per_case[code][reviewer] = decision
    result = {}
    for code, votes in per_case.items():
        if len(votes) < required:
            status = "incomplete"
        elif any(v == "reject" for v in votes.values()):
            status = "rejected"
        else:
            status = "all_approved"
        result[code] = {"status": status, "reviewers": sorted(votes), "approve": sum(v == "approve" for v in votes.values()),
                        "reject": sum(v == "reject" for v in votes.values())}
    return {
        "required_reviewers": required, "cases": result, "errors": errors,
        "note": "all_approved 는 승인 준비됨을 뜻할 뿐 승인이 아닙니다. 승인표 등록은 사용자·Codex 별도 승인 뒤 사람이 합니다.",
    }


# ── 명령줄 ──────────────────────────────────────────────────────────────────────────────
def _prepare(args) -> int:
    if not args.trusted_local:
        print("거절: 원본 pickle 은 신뢰하는 로컬 자료만 읽습니다. 그런 자료일 때만 --trusted-local 을 붙이세요.", file=sys.stderr)
        return 2
    rows = {row.volume_filename: row for row in parse_metadata(Path(args.metadata).read_text(encoding="utf-8"))}
    volumes = Path(args.volumes)
    cases = []
    for number, name in enumerate(args.volume_file, start=1):
        if not VOLUME_FILE.fullmatch(name) or name not in rows:
            print(f"거절: 메타데이터에 없는 볼륨 파일입니다({name}).", file=sys.stderr)
            return 2
        volume = load_volume_trusted(volumes / name, trusted_local=True)
        cases.append(build_case(volume, rows[name], f"case-{number:03d}", count=args.slices))
    report = write_cases(cases, args.out, private_dir=args.private_dir)
    print(json.dumps({"cases": report["cases"], "sheet": report["sheet"], "private": report["private"],
                      "status": "CANDIDATE_ONLY_NOT_APPROVED"}, ensure_ascii=False))
    return 0


def _summarize(args) -> int:
    out = Path(args.out)
    candidates = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("case-*/candidate.json"))]
    reviews = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.reviews).glob("review_*.json"))]
    print(json.dumps(summarize_reviews(candidates, reviews), ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="무릎 대표 영상 후보·검수 자료 도구(후보만 만들고 승인하지 않는다)")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="후보 PNG·검수 시트를 새 폴더에 만든다")
    prep.add_argument("--metadata", required=True)
    prep.add_argument("--volumes", required=True)
    prep.add_argument("--volume-file", action="append", required=True)
    prep.add_argument("--out", required=True)
    prep.add_argument("--slices", type=int, default=DEFAULT_SLICES)
    prep.add_argument("--private-dir", default=None,
                      help="(선택) 환자 연결 정보를 둘 폴더. 공유 출력 폴더의 바깥이어야 한다. 주지 않으면 연결 정보는 만들지 않는다")
    prep.add_argument("--trusted-local", action="store_true", help="원본 pickle 이 신뢰하는 로컬 자료임을 확인한다")
    summ = sub.add_parser("summarize", help="검수자 결과 일치 여부를 보고한다(승인하지 않는다)")
    summ.add_argument("--out", required=True)
    summ.add_argument("--reviews", required=True)
    args = parser.parse_args(argv)
    try:
        return _prepare(args) if args.command == "prepare" else _summarize(args)
    except CandidateError as exc:
        print(f"거절: {exc}", file=sys.stderr)
        return 2
