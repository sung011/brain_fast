"""
MedGemma 기반 한국어 영상 판독문 생성 서비스.

- api(기본): Hugging Face Inference API — 로컬 다운로드 없음 (토큰에 Inference 권한 필요)
- template: 앙상블·overlay 로 간결 판독 (API·다운로드 없음)
- local: transformers pipeline (약 8GB 다운로드)
"""
from __future__ import annotations

import base64
import io
import threading

import numpy as np
from PIL import Image

from .config import (
    HF_TOKEN,
    MEDGEMMA_BACKEND,
    MEDGEMMA_DUAL_IMAGE,
    MEDGEMMA_FALLBACK_TEMPLATE,
    MEDGEMMA_MAX_NEW_TOKENS,
    MEDGEMMA_MODEL_ID,
)
from .report_format import (
    build_report_extra,
    build_template_report,
    make_radiology_prompt,
    merge_ai_reference,
    normalize_korean_report,
)

_HF_INFERENCE_FORBIDDEN = (
    "HF 토큰에 Inference API 권한이 없습니다. "
    "https://huggingface.co/settings/tokens 에서 Fine-grained 토큰을 만들고 "
    "'Make calls to Inference Providers'(또는 Serverless Inference) 권한을 켜세요. "
    "또는 .env 에 MEDGEMMA_BACKEND=template 로 API 없이 앙상블 요약만 사용할 수 있습니다."
)


class MedGemmaService:
    """MedGemma 판독 — HF API / template / local."""

    def __init__(self) -> None:
        self._pipe = None
        self._client = None
        self._lock = threading.Lock()
        self.ready = False
        self.error: str | None = None
        self.device: str | None = None
        self.dtype: str | None = None
        self.backend = MEDGEMMA_BACKEND
        self._last_source: str = self.backend

    def load(self) -> None:
        if self.ready:
            return
        with self._lock:
            if self.ready:
                return
            try:
                if self.backend == "local":
                    if not HF_TOKEN:
                        raise ValueError(
                            "MedGemma local 은 gated 모델입니다. .env에 HF_TOKEN을 설정하세요."
                        )
                    self._load_local()
                elif self.backend == "template":
                    self._load_template()
                else:
                    if not HF_TOKEN:
                        raise ValueError(
                            "MedGemma API 는 HF_TOKEN 이 필요합니다. .env 또는 "
                            "MEDGEMMA_BACKEND=template 을 사용하세요."
                        )
                    self._load_api()
                self.ready = True
                self.error = None
            except Exception as exc:
                self.ready = False
                self.error = str(exc)
                raise

    def _load_template(self) -> None:
        self._client = None
        self._pipe = None
        self.device = "ensemble-template"
        self.dtype = None

    def _load_api(self) -> None:
        from huggingface_hub import InferenceClient

        self._client = InferenceClient(
            model=MEDGEMMA_MODEL_ID,
            token=HF_TOKEN,
            provider="hf-inference",
        )
        self._pipe = None
        self.device = "huggingface-inference-api"
        self.dtype = None

    @staticmethod
    def _resolve_device_dtype():
        import torch

        if torch.cuda.is_available():
            return "cuda", torch.bfloat16
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps", torch.bfloat16
        return "cpu", torch.float32

    def _load_local(self) -> None:
        from transformers import pipeline

        device, dtype = self._resolve_device_dtype()
        self.device = device
        self.dtype = str(dtype)
        self._client = None
        self._pipe = pipeline(
            "image-text-to-text",
            model=MEDGEMMA_MODEL_ID,
            dtype=dtype,
            device=device,
            token=HF_TOKEN or None,
        )

    def _ensure_loaded(self) -> None:
        if not self.ready:
            self.load()

    @staticmethod
    def _to_pil_rgb(arr: np.ndarray) -> Image.Image:
        if arr.dtype != np.uint8:
            if float(np.nanmax(arr)) <= 1.5:
                arr = (np.clip(arr, 0, 1) * 255).astype(np.uint8)
            else:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        return Image.fromarray(arr).convert("RGB")

    @staticmethod
    def _pil_to_data_url(pil: Image.Image) -> str:
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    def _build_messages(
        self,
        original_uint8: np.ndarray,
        overlay_uint8: np.ndarray | None,
        extra: str,
    ) -> list[dict]:
        use_dual = MEDGEMMA_DUAL_IMAGE and overlay_uint8 is not None
        prompt = make_radiology_prompt(extra, dual_image=use_dual)

        content: list[dict] = []
        if use_dual:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._pil_to_data_url(self._to_pil_rgb(original_uint8))},
                }
            )
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._pil_to_data_url(self._to_pil_rgb(overlay_uint8))},
                }
            )
        else:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._pil_to_data_url(self._to_pil_rgb(original_uint8))},
                }
            )
        content.append({"type": "text", "text": prompt})
        return [{"role": "user", "content": content}]

    @staticmethod
    def _is_forbidden(exc: Exception) -> bool:
        text = str(exc).lower()
        return "403" in text or "forbidden" in text or "sufficient permissions" in text

    def _generate_api(self, messages: list[dict]) -> str:
        assert self._client is not None
        try:
            response = self._client.chat_completion(
                messages=messages,
                max_tokens=MEDGEMMA_MAX_NEW_TOKENS,
                temperature=0,
            )
            content = response.choices[0].message.content
            if content:
                self._last_source = "api"
                return content
        except Exception as exc:
            if not self._is_forbidden(exc):
                raise
            legacy = self._generate_legacy_api(messages)
            if legacy:
                self._last_source = "api-legacy"
                return legacy
            raise ValueError(_HF_INFERENCE_FORBIDDEN) from exc

        raise RuntimeError("MedGemma Inference API가 빈 응답을 반환했습니다.")

    def _generate_legacy_api(self, messages: list[dict]) -> str | None:
        if not HF_TOKEN:
            return None
        try:
            import httpx
            from huggingface_hub.constants import INFERENCE_ENDPOINT

            url = f"{INFERENCE_ENDPOINT}/models/{MEDGEMMA_MODEL_ID}/v1/chat/completions"
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {HF_TOKEN}"},
                json={
                    "model": MEDGEMMA_MODEL_ID,
                    "messages": messages,
                    "max_tokens": MEDGEMMA_MAX_NEW_TOKENS,
                    "temperature": 0,
                },
                timeout=120.0,
            )
            if resp.status_code == 403:
                return None
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content if content else None
        except Exception:
            return None

    def _generate(self, messages: list[dict]) -> str:
        if self.backend == "local":
            assert self._pipe is not None
            output = self._pipe(
                text=messages,
                max_new_tokens=MEDGEMMA_MAX_NEW_TOKENS,
                generate_kwargs={
                    "do_sample": False,
                    "repetition_penalty": 1.25,
                    "no_repeat_ngram_size": 4,
                },
            )
            self._last_source = "local"
            return output[0]["generated_text"][-1]["content"]

        return self._generate_api(messages)

    def explain(
        self,
        original_uint8: np.ndarray,
        overlay_uint8: np.ndarray | None,
        probs: list[float],
        per_class: dict,
        targets: list[int],
        clinical: str | None = None,
    ) -> str:
        self._ensure_loaded()

        if self.backend == "template":
            self._last_source = "template"
            report = build_template_report(probs, per_class, targets)
            return merge_ai_reference(report, probs)

        extra = build_report_extra(probs, per_class, targets, clinical=clinical)
        messages = self._build_messages(original_uint8, overlay_uint8, extra)
        try:
            raw_report = self._generate(messages)
        except Exception:
            if MEDGEMMA_FALLBACK_TEMPLATE:
                self._last_source = "template-fallback"
                report = build_template_report(probs, per_class, targets)
                return merge_ai_reference(report, probs)
            raise

        report = normalize_korean_report(raw_report, probs, per_class, targets)
        return merge_ai_reference(report, probs)

    @staticmethod
    def decode_overlay_base64(b64: str) -> np.ndarray:
        data = base64.b64decode(b64)
        return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


medgemma_service = MedGemmaService()
