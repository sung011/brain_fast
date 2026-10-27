"""
무릎 MRI 앙상블 분류 서비스.

이상 소견 / ACL / 반월판 세 가지를 촬영면(axial·coronal·sagittal)별 모델로 예측하고,
면별 확률을 평균 → 보정 → "판단 어려움" 구간 판정까지 한다.

- 소견마다 결합기(combiner.json)가 정한 촬영면을 쓴다. 필요한 면이 하나도 없으면
  그 소견은 status="not_evaluated" 로 이유와 함께 알리고, 낮은 확률로 채우지 않는다.
- 입력이 이상하면 KneeInputError, 계산 결과가 NaN·범위 밖이면 KneeAnalysisError.
- 판정(게이팅)은 반올림 전 확률로 하고, 화면에 보여줄 prob 만 소수 셋째 자리로 반올림한다.

가중치: config.hf_repo() (Hugging Face). 첫 사용 때 내려받아 캐시에 저장한다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import torch
from huggingface_hub import hf_hub_download

from .config import (
    CALIBRATION_FILE,
    CKPT_FILES,
    COMBINER_FILE,
    GATING_BAND,
    PLANES,
    TASKS,
    hf_download_token,
    hf_repo,
    hf_revision,
    safe_failure_text,
)
from .constants import TASK_KO, UNCERTAIN_VERDICT, VERDICT
from .errors import KneeAnalysisError, KneeInputError, KneeModelLoadError, KneeModelNotReady
from .model import INFERENCE_LOCK, MRNetModel
from .preprocess import Preprocess

_EPS = 1e-6


def _logit(p: float) -> float:
    p = float(np.clip(p, _EPS, 1 - _EPS))
    return float(np.log(p / (1 - p)))


def _sigmoid(z: float) -> float:
    return float(1.0 / (1.0 + np.exp(-z)))


def _check_probability(value: float, what: str) -> float:
    if not np.isfinite(value) or not 0.0 <= value <= 1.0:
        raise KneeAnalysisError(f"{what} 확률이 정상 범위(0~1)가 아닙니다: {value}")
    return float(value)


class KneeEnsembleService:
    def __init__(self) -> None:
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.models: dict[tuple[str, str], list[tuple[MRNetModel, float | None]]] = {}
        self.combiner: dict = {}
        self.calibration: dict = {}
        self.ready = False
        self.error: str | None = None
        self._load_lock = threading.Lock()

    @staticmethod
    def _download(filename: str) -> Path:
        repo = hf_repo()
        if not repo:
            raise RuntimeError("KNEE_HF_REPO 가 비어 있습니다. 환경 변수나 .env 에 KNEE_HF_REPO=계정/저장소 를 적어 주세요.")
        # token: KNEE_HF_TOKEN 이 있으면 그 값, 없으면 False(토큰을 보내지 않음). None 을 넘기면 뇌 토큰·로그인 캐시가 몰래 쓰인다.
        try:
            path = hf_hub_download(repo_id=repo, filename=filename, revision=hf_revision(), token=hf_download_token())
        except Exception as exc:
            failure = KneeModelLoadError(safe_failure_text(exc))
        else:
            return Path(path)
        raise failure  # except 블록 밖에서 던져 원래 예외(토큰이 섞일 수 있음)가 cause/context 로 남지 않게 한다

    def load(self) -> None:
        with self._load_lock:
            if self.ready:
                return
            failure: KneeModelLoadError | None = None
            try:
                models: dict = {}
                for (task, plane), files in CKPT_FILES.items():
                    members = []
                    for fname in files:
                        ck = torch.load(self._download(fname), map_location=self.device, weights_only=True)
                        m = MRNetModel(backbone=ck.get("backbone", "resnet18"), pool=ck.get("pool", "max"))
                        m.load_state_dict(ck["model"])
                        m.to(self.device).eval()
                        members.append((m, ck.get("crop")))
                    models[(task, plane)] = members
                combiner = json.loads(self._download(COMBINER_FILE).read_text(encoding="utf-8"))
                calibration = json.loads(self._download(CALIBRATION_FILE).read_text(encoding="utf-8"))
            except Exception as exc:
                # 이미 정리된 예외(내려받기 실패)는 그대로, 그 밖(체크포인트 손상 등)은 종류와 가린 문구만 담아 새로 만든다.
                failure = exc if isinstance(exc, KneeModelLoadError) else KneeModelLoadError(safe_failure_text(exc))
            if failure is not None:
                self.ready = False
                self.error = str(failure)
                raise failure  # except 블록 밖: 원 예외를 cause/context/traceback 으로 다시 노출하지 않는다
            self.models, self.combiner, self.calibration = models, combiner, calibration
            self.ready = True
            self.error = None

    def _require_ready(self) -> None:
        if not self.ready:
            raise RuntimeError(self.error or "무릎 모델이 아직 로드되지 않았습니다.")

    def ensure_ready(self) -> None:
        """API 용: 모델이 없으면 불러오고, 실패하면 내부 원인(저장소 이름·경로 등)을 숨기고 KneeModelNotReady 만 낸다."""
        if self.ready:
            return
        try:
            self.load()
        except Exception:
            raise KneeModelNotReady("무릎 모델을 준비하지 못했습니다.") from None

    def single_plane_prob(self, task: str, plane: str, gray: np.ndarray) -> float:
        """한 촬영면의 이미지 한 장(S=1 볼륨)에 대한 원래 모델 점수(보정 전). 공개 여부는 gates 가 정한다."""
        self.ensure_ready()
        if (task, plane) not in self.models:
            raise KneeInputError("이 촬영면에는 해당 소견 모델이 없습니다.")
        volume = Preprocess.validate_volume(np.ascontiguousarray(gray)[None, ...])
        return _check_probability(self._plane_prob(task, plane, volume), f"{task}/{plane}")

    def single_plane_gradcam(self, task: str, plane: str, gray: np.ndarray):
        """이미지 한 장의 Grad-CAM 오버레이. 반환: (PIL 이미지, 강조했는지). 그림은 모델이 본 224×224 입력 위에 그려진다."""
        from .localization import Localization

        self.ensure_ready()
        if (task, plane) not in self.models:
            raise KneeInputError("이 촬영면에는 해당 소견 모델이 없습니다.")
        model, crop = self.models[(task, plane)][0]
        volume = Preprocess.validate_volume(np.ascontiguousarray(gray)[None, ...])
        cam, _prob, _top = Localization.grad_cam(model, volume, crop, self.device, n_slices=1)
        shown = Preprocess.to_gray01(Preprocess.preprocess_volume(volume, crop=crop)[0])
        return Localization.build_overlay(shown, cam[0])

    @torch.no_grad()
    def _plane_prob(self, task: str, plane: str, volume: np.ndarray) -> float:
        probs = []
        with INFERENCE_LOCK:
            for model, crop in self.models[(task, plane)]:
                x = Preprocess.preprocess_volume(volume, crop=crop).to(self.device)
                probs.append(torch.sigmoid(model(x)).item())
        return float(np.mean(probs))

    def _calibrate(self, task: str, p: float) -> float:
        c = self.calibration.get(task)
        if not c:
            return p
        a, b = c.get("platt_a", 1.0), c.get("platt_b", 0.0)
        return _check_probability(_sigmoid(a * _logit(p) + b), f"{task} 보정 후")

    @staticmethod
    def judge(task: str, p: float) -> tuple[bool, str]:
        """보정된 확률 p(반올림 전) → (확신 여부, 문구). 경계 0.4·0.6 은 '판단 어려움'에 포함한다."""
        lo, hi = GATING_BAND[task]
        confident = not (lo <= p <= hi)
        high_txt, low_txt = VERDICT[task]
        verdict = UNCERTAIN_VERDICT if not confident else (high_txt if p >= 0.5 else low_txt)
        return confident, verdict

    def _validated_inputs(self, planes_vol: dict) -> dict[str, np.ndarray]:
        if not isinstance(planes_vol, dict) or not planes_vol:
            raise KneeInputError("촬영면별 볼륨이 하나도 없습니다.")
        unknown = [pl for pl in planes_vol if pl not in PLANES]
        if unknown:
            raise KneeInputError(f"알 수 없는 촬영면입니다: {unknown}. 사용 가능: {list(PLANES)}")
        return {pl: Preprocess.validate_volume(vol) for pl, vol in planes_vol.items()}

    def predict(self, planes_vol: dict[str, np.ndarray]) -> dict[str, dict]:
        """planes_vol: {"axial": (S,H,W), "coronal": ..., "sagittal": ...} (일부만 줘도 된다).

        반환: 소견마다 하나. status 가 "evaluated" 면 prob·confident·verdict·planes_used·missing_planes,
        "not_evaluated" 면 reason·required_planes·missing_planes 를 담는다.
        """
        self._require_ready()
        volumes = self._validated_inputs(planes_vol)
        result: dict[str, dict] = {}
        for task in TASKS:
            required = [
                pl for pl in self.combiner.get(task, {}).get("planes", PLANES) if (task, pl) in self.models
            ]
            use = [pl for pl in required if pl in volumes]
            missing = [pl for pl in required if pl not in volumes]
            if not use:
                result[task] = {
                    "label_ko": TASK_KO[task],
                    "status": "not_evaluated",
                    "reason": "이 소견에 필요한 촬영면이 없어 판단하지 않았습니다.",
                    "required_planes": required,
                    "missing_planes": missing,
                }
                continue
            plane_probs = [
                _check_probability(self._plane_prob(task, pl, volumes[pl]), f"{task}/{pl}") for pl in use
            ]
            p = self._calibrate(task, float(np.mean(plane_probs)))
            confident, verdict = self.judge(task, p)
            result[task] = {
                "label_ko": TASK_KO[task],
                "status": "evaluated",
                "prob": round(p, 3),
                "confident": confident,
                "verdict": verdict,
                "planes_used": use,
                "missing_planes": missing,
            }
        return result


knee_service = KneeEnsembleService()
