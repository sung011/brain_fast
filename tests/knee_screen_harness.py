"""
무릎 학습 화면(GET /learn/knee) 연결 시험용 "별도 시험 환경".

운영 앱(main.py)과 별개의 작은 FastAPI 앱을 만들고, 아래를 **이 시험 환경에만** 둔다.
- 임시 내장 PostgreSQL(pgserver) 한 개: 프로젝트 DB·호스팅 DB·.env 와 무관하다. 끝나면 지운다.
- 시험 전용 로그인(/_login): 학습자 세션(session["learner"])을 만든다. 운영 코드에는 이런 우회가 없다.
- 합성 영상(PNG)과 시험 전용 영상 제공 경로(/_images/...), 그 경로를 알려 주는 영상 공급자(set_asset_provider).
- 임시로 채운 승인표(APPROVED_SLIDE_REFERENCES)와 가짜 서명 키·자산 루트(환경 변수). 시험이 끝나면 되돌린다.
실제 KneeMRI 영상·실제 승인·실제 NAS·실제 프로젝트 DB 는 쓰지 않는다. 모든 값은 가짜다.

사용:
  - pytest: tests/test_knee_screen.py 가 Harness 를 만들어 TestClient 로 호출한다.
  - 브라우저 시험: python tests/knee_screen_harness.py --port 8012  (임시 DB 를 띄우고 서버를 켠다. Ctrl+C 로 끝)
여러 장 모드(`series=True`, 브라우저 시험은 기본 켜짐·`--no-series` 로 끔): 합성 24장을 임시 폴더에 PNG 로 쓰고(`KNEE_IMAGE_DIR`) 열람 자산 등록부
(`APPROVED_SERIES_ASSETS`)를 임시로 채운다. 그러면 운영과 같은 서버 폴더 공급원 코드(class/knee/local_assets.py)가 `GET /learning/knee/series` 와
장별 영상(`/learning/knee/series/.../image/N`)을 실제로 제공한다. 지연·손상 주입은 이 시험 앱의 시험 전용 주소(`/_series_delay`, `/_series_break`)로만 한다
(제품 코드에는 지연·손상 훅이 없다).
필요: pip install pgserver "psycopg[binary]" pillow
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time as real_time
from importlib import import_module
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from db import Base, get_db
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from routers import commonness, kneeLearning
from schemas.userSchemas import UserCreate
from services import userServices as user_service

knee_config = import_module("class.knee.config")
slide_refs = import_module("class.knee.slide_references")
slice_manifest = import_module("class.knee.slice_manifest")

ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = "/_knee_assets/"
KEY = "synthetic-harness-key-0123456789abcdef0123456789"   # 가짜 서명 키(32자 이상)
IMAGE_W, IMAGE_H = 320, 384    # 일부러 정사각형이 아니게 한다(여백·비율 오류를 드러내려고)


def synthetic_png(seed: int, width: int = IMAGE_W, height: int = IMAGE_H) -> bytes:
    """합성 영상. 환자 영상이 아니라 무늬만 있는 PNG."""
    rng = np.random.default_rng(seed)
    base = rng.integers(20, 90, (height, width), dtype=np.uint8)
    yy, xx = np.mgrid[0:height, 0:width]
    blob = (((xx - width * 0.5) ** 2) / (width * 0.28) ** 2 + ((yy - height * 0.45) ** 2) / (height * 0.2) ** 2) < 1
    base[blob] = np.clip(base[blob] + 90, 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(base, "L").save(buffer, "PNG")
    return buffer.getvalue()


class Clock:
    """시험용 시계. 토큰 만료를 기다리지 않고 시간을 앞당겨 확인한다(kneeLearning 이 쓰는 time.time 만 바꾼다)."""

    def __init__(self):
        self.offset = 0

    def time(self) -> float:
        return real_time.time() + self.offset


class Harness:
    """임시 DB·합성 데이터·시험용 앱을 한 번에 만든다. close() 가 모두 되돌린다."""

    def __init__(self, *, slice_count: int = 24, rep: int = 12, ref_box=(0.3, 0.3, 0.4, 0.4), approve: bool = True, series: bool = False,
                 real_images=None, real_notice: str = "", extra_cases=(), guest_origins: str = ""):
        """real_images: 합성 대신 쓸 PNG 바이트 목록(등록 배열 순서). 외부 도구가 만든 **실제 자료 형식 시험**용(임시 폴더·임시 DB 안에서만 쓰고 끝나면 지운다).

        주면 장 수·치수는 그 영상에서 정해지고, 열람 등록의 kind 는 "real"(안내 문구는 real_notice)이 된다. 대표 번호(rep)·기준 박스(ref_box)는 호출한 쪽이 넣는다.
        """
        self._stack = contextlib.ExitStack()
        self.series = series
        # 여러 케이스 무작위 출제 시험(여러 장 모드에서만): 장 수가 다른 합성 케이스를 더 등록한다. 첫 추가 케이스에는 **그 케이스 자신의**
        # 기준(다른 장·다른 박스)을 넣고, 나머지는 기준 없이 열람만 된다(채점 안 함). 다른 케이스의 기준을 복사하지 않는다.
        self.extra_counts = tuple(int(c) for c in extra_cases) if series else ()
        self.extra = []   # [{"study_idx","count","paths","manifest","ref"|None}]
        # 게스트 열람 시험(MSN-20261016-002): 허용 Origin 을 주면 이 시험 프로세스 환경에서만 게스트 스위치를 켠다(실제 .env 무관)
        self.guest_origins = guest_origins
        self.real_images = list(real_images) if real_images else None
        self.real_notice = real_notice
        if self.real_images:
            slice_count = len(self.real_images)
            with Image.open(io.BytesIO(self.real_images[0])) as probe:
                self.image_w, self.image_h = probe.size
        else:
            self.image_w, self.image_h = IMAGE_W, IMAGE_H
        try:
            self._setup(slice_count, rep, ref_box, approve)
        except BaseException:
            self._stack.close()
            raise

    def _setup(self, slice_count, rep, ref_box, approve):
        import pgserver

        self.tmp = Path(tempfile.mkdtemp(prefix="knee_screen_pg_"))
        self._stack.callback(shutil.rmtree, self.tmp, ignore_errors=True)
        server = pgserver.get_server(self.tmp / "pg", cleanup_mode="stop")
        self._stack.callback(server.cleanup)
        uri = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_engine(uri)
        self._stack.callback(self.engine.dispose)
        # 이 시험에 필요한 세 테이블만 만든다(프로젝트의 다른 테이블·정식 DDL 은 건드리지 않는다).
        Base.metadata.create_all(self.engine, tables=[UserModel.__table__, StudyModel.__table__, ReviewNodeModel.__table__])
        self.Session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

        # 합성 데이터 --------------------------------------------------------
        self.paths = [f"{ASSET_ROOT}study/{i:03d}.png" for i in range(slice_count)]
        self.png_by_path = {p: (self.real_images[i] if self.real_images else synthetic_png(i)) for i, p in enumerate(self.paths)}
        with self.Session() as db:
            def user(user_id):
                row = UserModel(user_id=user_id, user_pw="synthetic-not-a-password", user_birth="2000-01-01", user_un="t", user_sp="t")
                db.add(row)
                db.flush()
                return row.idx
            self.user_idx, self.other_user_idx = user("harness-learner-1"), user("harness-learner-2")
            self.gone_user_idx = user("harness-learner-gone")
            # 실제 POST /login 으로 로그인할 수 있는 합성 회원(비밀번호는 bcrypt 로 저장된다). 가짜 값이며 실제 계정이 아니다.
            self.login_id, self.login_pw = "harness-login@example.test", "synthetic-pass-1234"
            self.login_idx = user_service.create_user(db, UserCreate(
                user_id=self.login_id, user_pw=self.login_pw, user_name="합성", user_birth="2000-01-01", user_un="합성대", user_sp="합성과")).idx
            gone = db.get(UserModel, self.gone_user_idx)
            gone.del_yn = "Y"
            slides = json.dumps(self.paths)

            def study(part="4", modal="3", image=slides):
                row = StudyModel(st_part=part, st_modal=modal, st_disease="ACL", st_image=image)
                db.add(row)
                db.flush()
                return row.idx
            self.study_idx = study()                      # 대표 승인이 있는 무릎 슬라이드 문제
            self.unapproved_study_idx = study()           # 승인 없는 무릎 슬라이드 문제
            self.brain_study_idx = study(part="1", modal="2")   # 무릎이 아닌 문제
            for k, count in enumerate(self.extra_counts, start=1):
                paths = [f"{ASSET_ROOT}case{k}/{i:03d}.png" for i in range(count)]
                for i, path_ in enumerate(paths):
                    self.png_by_path[path_] = synthetic_png(1000 * k + i)
                self.extra.append({"study_idx": study(image=json.dumps(paths)), "count": count, "paths": paths})
            db.commit()

        self.rep = rep
        self.ref_box = ref_box
        self.manifest = slice_manifest.build_manifest(json.dumps(self.paths), (ASSET_ROOT,))
        self.rep_png = self.png_by_path[self.paths[rep]]
        self.rep_sha = hashlib.sha256(self.rep_png).hexdigest()
        self.fault = {"provider": False, "image": False}   # 시험 전용: 영상 공급자 실패 / 영상 파일 404
        self.images_by_ref_id = {7: self.rep_png}
        self.ref = slide_refs.SlideReference(
            reference_id=7, study_idx=self.study_idx, manifest_version=self.manifest.version,
            slice_set_sha256=self.manifest.list_sha256, slice_no=rep, image_path=self.paths[rep],
            image_sha256=self.rep_sha, plane="sagittal", x=ref_box[0], y=ref_box[1], width=ref_box[2], height=ref_box[3],
            source="synthetic dataset record v0", version="v1", reviewed_by="reviewer-000", reviewed_at="2026-10-12T00:00:00",
            image_width=self.image_w, image_height=self.image_h,   # 승인 치수(영상 공급원을 쓸 때 필수)
        )

        # 환경 변수·승인표·영상 공급자는 시험 동안만 채운다 -----------------------------
        env = {
            "KNEE_ROI_GRADE_ENABLED": "1", "KNEE_ASSET_ROOTS": ASSET_ROOT,
            "KNEE_PROBLEM_TOKEN_KEY": KEY, "KNEE_PROBLEM_TOKEN_TTL_S": "7200",
        }
        if self.guest_origins:
            env.update(KNEE_GUEST_VIEW_ENABLED="1", KNEE_GUEST_ALLOWED_ORIGINS=self.guest_origins, KNEE_GUEST_COOKIE_SECURE="0")
        import_module("services.kneeGuestViewServices").reset_store()   # 게스트 상태는 시험마다 새로
        self.image_dir = None
        self.delays = {}          # 시험 전용: 장 번호 → 응답 지연(ms). 이 시험 앱의 미들웨어만 쓴다
        self.broken = {}          # 시험 전용: 장 번호 → 원래 바이트(손상 중인 장)
        if self.series:
            self.image_dir = self.tmp / "images"
            for path_, data in self.png_by_path.items():
                target = self.image_dir / path_.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            env["KNEE_IMAGE_DIR"] = str(self.image_dir)
        self._stack.enter_context(mock.patch.dict(os.environ, env))
        self._stack.enter_context(mock.patch.object(knee_config, "ENV_PATH", Path(os.devnull) / "없음.env"))
        self._stack.enter_context(mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {}, clear=True))   # 끝나면 원래 빈 표로 복원
        if approve:
            slide_refs.APPROVED_SLIDE_REFERENCES[self.study_idx] = [self.ref]
        if self.series:
            series_assets = import_module("class.knee.series_assets")
            self._stack.enter_context(mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {}, clear=True))   # 끝나면 원래 빈 표로 복원
            series_assets.APPROVED_SERIES_ASSETS[self.study_idx] = [self.series_row()]
            for n, case in enumerate(self.extra):
                case["manifest"] = slice_manifest.build_manifest(json.dumps(case["paths"]), (ASSET_ROOT,))
                series_assets.APPROVED_SERIES_ASSETS[case["study_idx"]] = [self.extra_series_row(case)]
                case["ref"] = None
                if n == 0 and approve:
                    slot = case["count"] // 3
                    png = self.png_by_path[case["paths"][slot]]
                    case["ref"] = slide_refs.SlideReference(
                        reference_id=100 + n, study_idx=case["study_idx"], manifest_version=case["manifest"].version,
                        slice_set_sha256=case["manifest"].list_sha256, slice_no=slot, image_path=case["paths"][slot],
                        image_sha256=hashlib.sha256(png).hexdigest(), plane="sagittal", x=0.1, y=0.15, width=0.3, height=0.25,
                        source="synthetic dataset record extra", version="v1", reviewed_by="reviewer-000", reviewed_at="2026-10-15T00:00:00",
                        image_width=self.image_w, image_height=self.image_h,
                    )
                    slide_refs.APPROVED_SLIDE_REFERENCES[case["study_idx"]] = [case["ref"]]
                    self.images_by_ref_id[case["ref"].reference_id] = png

        def provider(ref, manifest):
            if self.fault["provider"]:
                raise RuntimeError("synthetic provider outage")
            return {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": self.image_w, "image_height": self.image_h}

        kneeLearning.set_asset_provider(provider)
        self._stack.callback(kneeLearning.set_asset_provider, None)
        # 합성 영상은 이 하네스의 `/_images` 로 제공되므로 NAS/폴더 공급원이 없다. 이 시험 환경에서만 "영상 검증 생략"을 **명시로** 주입한다.
        # 운영 코드에는 검증 생략 경로가 없다(공급원이 없으면 제출이 닫힌다). 공급원 검증을 시험하는 쪽은 이 주입을 해제하고 시작한다.
        kneeLearning.set_asset_verifier(lambda reference, manifest: None)
        self._stack.callback(kneeLearning.set_asset_verifier, None)
        self.clock = Clock()
        self._stack.enter_context(mock.patch.object(kneeLearning, "time", self.clock))

        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="harness-session-secret")

        @app.middleware("http")
        async def _series_delay_middleware(request: Request, call_next):
            """시험 전용: 지정한 장의 영상 응답을 늦춘다(빠른 스크롤에서 응답이 뒤바뀌는 상황 재현). 운영 코드에는 없다."""
            match = re.search(r"/learning/knee/series/\d+/[0-9a-f]+/image/(\d+)$", request.url.path)
            if match and self.delays.get(int(match.group(1))):
                await asyncio.sleep(self.delays[int(match.group(1))] / 1000)
            return await call_next(request)

        self.random_fault = {"ms": 0, "status": 0}   # 시험 전용: 무작위 고르기 응답 지연(ms)·강제 상태코드(401/503 등)

        @app.middleware("http")
        async def _random_fault_middleware(request: Request, call_next):
            """시험 전용: GET /learning/knee/random 응답을 늦추거나 실패로 바꾼다(늦은 응답·로그인 만료·서버 오류 재현). 운영 코드에는 없다."""
            if request.url.path == "/learning/knee/random":
                if self.random_fault["ms"]:
                    await asyncio.sleep(self.random_fault["ms"] / 1000)
                if self.random_fault["status"]:
                    return Response(content=json.dumps({"detail": "synthetic"}), status_code=self.random_fault["status"], media_type="application/json")
            return await call_next(request)

        @app.post("/_guest_reset")
        async def _guest_reset(meta: int = 0, cases: int = 0):
            """시험 전용: 게스트 상태를 모두 비운다(만료·서버 재시작 재현). meta>0 이면 메타데이터 한도를, cases>0 이면 케이스 한도를 작게. 운영 코드에는 없다."""
            svc = import_module("services.kneeGuestViewServices")
            over = {}
            if meta > 0:
                over.update(meta_per_guest_per_min=meta, meta_per_ip_per_min=meta * 3)
            if cases > 0:
                over.update(max_cases=cases)
            svc.reset_store(svc.GuestLimits(**over) if over else None)
            return {"reset": True, "meta": meta, "cases": cases}

        @app.post("/_guest_switch")
        async def _guest_switch(on: int = 1):
            """시험 전용: 이 시험 프로세스의 게스트 스위치만 켜고 끈다(실제 .env 무관)."""
            os.environ["KNEE_GUEST_VIEW_ENABLED"] = "1" if on else "0"
            return {"enabled": bool(on)}

        @app.post("/_random_fault")
        async def _random_fault(ms: int = 0, status: int = 0):
            self.random_fault.update(ms=max(0, ms), status=status if status in (401, 403, 500, 503) else 0)
            return self.random_fault

        @app.post("/_series_delay")
        async def _series_delay(slice: int, ms: int = 0):
            if ms > 0:
                self.delays[slice] = ms
            else:
                self.delays.pop(slice, None)
            return {"delays": self.delays}

        @app.post("/_series_delay_clear")
        async def _series_delay_clear():
            self.delays.clear()
            return {"delays": self.delays}

        @app.post("/_series_break")
        async def _series_break(slice: int, fix: int = 0):
            if self.image_dir is None:
                return {"error": "series mode is off"}
            self.fix_slice(slice) if fix else self.break_slice(slice)
            return {"broken": sorted(self.broken)}

        @app.get("/_series_hashes")
        def _series_hashes():
            """시험 전용: 합성 장별 영상의 SHA-256(화면이 실제로 보여 주는 영상이 그 장의 것인지 대조하려고)."""
            return [hashlib.sha256(self.png_by_path[p]).hexdigest() for p in self.paths]

        @app.post("/_grading")
        async def _grading(enabled: int = 1):
            """시험 전용: 이 시험 프로세스의 채점 스위치(KNEE_ROI_GRADE_ENABLED)를 켜고 끈다(운영 설정·.env 와 무관)."""
            os.environ["KNEE_ROI_GRADE_ENABLED"] = "1" if enabled else "0"
            return {"enabled": os.environ["KNEE_ROI_GRADE_ENABLED"]}

        @app.post("/_reference")
        async def _reference(remove: int = 0):
            """시험 전용: 대표 기준 승인을 철회/복구한다(기준이 없는 문제에서 영상 탐색이 되는지 확인)."""
            if remove:
                self.swap_reference(0, remove=True)
            else:
                slide_refs.APPROVED_SLIDE_REFERENCES[self.study_idx] = [self.ref]
            return {"approved": self.study_idx in slide_refs.APPROVED_SLIDE_REFERENCES}

        @app.post("/_series_mismatch")
        async def _series_mismatch(on: int = 1):
            """시험 전용: 대표 장 영상만 새 내용으로 바꾸고 열람 등록부도 그 새 해시로 갱신한다(채점 기준은 옛 해시 그대로).
            열람(영상 탐색)은 되고 채점 기준과는 맞지 않는 상태(FINDING-SERIES-002-01)를 만든다. on=0 이면 원래대로."""
            series_assets = import_module("class.knee.series_assets")
            target = self.image_dir / self.paths[self.rep].lstrip("/")
            if on:
                self.broken.setdefault(-1, target.read_bytes())
                png = synthetic_png(99, self.image_w, self.image_h)
                target.write_bytes(png)
                row = self.series_row()
                hashes = list(row.slice_sha256)
                hashes[self.rep] = hashlib.sha256(png).hexdigest()
                series_assets.APPROVED_SERIES_ASSETS[self.study_idx] = [self.series_row(slice_sha256=tuple(hashes), version="series-v2")]
            else:
                original = self.broken.pop(-1, None)
                if original is not None:
                    target.write_bytes(original)
                series_assets.APPROVED_SERIES_ASSETS[self.study_idx] = [self.series_row()]
            return {"mismatch": bool(on)}

        @app.post("/_series_registry")
        async def _series_registry(remove: int = 0):
            """시험 전용: 열람 자산 등록을 철회/복구한다(등록이 없을 때의 응답 확인)."""
            series_assets = import_module("class.knee.series_assets")
            if remove:
                series_assets.APPROVED_SERIES_ASSETS.pop(self.study_idx, None)
            else:
                series_assets.APPROVED_SERIES_ASSETS[self.study_idx] = [self.series_row()]
            return {"registered": self.study_idx in series_assets.APPROVED_SERIES_ASSETS}

        @app.post("/_login")
        async def _login(request: Request, idx: int, verified: bool = True):
            request.session["learner"] = {"idx": idx, "email_verified": verified}   # 시험 전용 우회. 운영 코드에는 없다.
            return {"ok": True}

        @app.get("/_start")
        async def _start(request: Request, idx: int | None = None):
            """시험 전용 편의 주소: 주소창에서 열면 시험 학습자로 로그인하고 실제 연결 화면으로 이동한다."""
            from fastapi.responses import RedirectResponse
            request.session["learner"] = {"idx": idx or self.user_idx, "email_verified": True}
            return RedirectResponse(f"/learn/knee?study_idx={self.study_idx}", status_code=303)

        @app.get("/_session")
        async def _session(request: Request):
            return dict(request.session)   # 시험 전용: 서버 세션에 무엇이 들어 있는지 확인

        @app.post("/_logout")
        async def _logout(request: Request):
            request.session.clear()
            return {"ok": True}

        @app.post("/_advance")
        async def _advance(seconds: int = 0, reset: bool = False):
            self.clock.offset = 0 if reset else self.clock.offset + seconds   # 시험 전용: 토큰 만료 확인용
            return {"offset": self.clock.offset}

        @app.get("/_reviews")
        def _reviews():
            return [{"idx": r.idx, "user": r.rn_u_idx, "study": r.rn_s_idx, "image": r.rn_image, "solution": r.rn_solution}
                    for r in self.reviews()]   # 시험 전용: 임시 DB 에 저장된 행 확인

        @app.post("/_swap_reference")
        async def _swap(rep: int, reference_id: int = 8, version: str = "v2"):
            """시험 전용: 승인된 대표를 다른 장·다른 기준 버전으로 바꾼다(대표 변경·자산 교체 상황 재현)."""
            self.swap_reference(rep, reference_id=reference_id, version=version)
            return {"rep": self.rep, "reference_id": reference_id, "version": version}

        @app.post("/_fault")
        async def _fault(provider: int | None = None, image: int | None = None):
            """시험 전용: 영상 공급자 실패(503 유도)·영상 파일 404 를 켜고 끈다."""
            if provider is not None:
                self.fault["provider"] = bool(provider)
            if image is not None:
                self.fault["image"] = bool(image)
            return self.fault

        @app.get("/_images/rep-{reference_id}.png")
        def _image(reference_id: int):
            if self.fault["image"] or reference_id not in self.images_by_ref_id:
                return Response(status_code=404)
            return Response(content=self.images_by_ref_id[reference_id], media_type="image/png")

        def get_session():
            with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = get_session
        app.include_router(commonness.router)   # 실제 POST /login(학습자 세션 발급)을 시험하려고 포함한다
        app.include_router(kneeLearning.router)
        app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
        return app

    # 시험 보조 --------------------------------------------------------------
    def series_row(self, **over):
        """합성 24장의 열람 자산 등록 행(kind=synthetic: 합성 시험임을 숨기지 않는다)."""
        series_assets = import_module("class.knee.series_assets")
        fields = dict(
            study_idx=self.study_idx, manifest_version=self.manifest.version, slice_set_sha256=self.manifest.list_sha256,
            slice_sha256=tuple(hashlib.sha256(self.png_by_path[p]).hexdigest() for p in self.paths),
            slice_sizes=tuple((self.image_w, self.image_h) for _ in self.paths),
            kind="real" if self.real_images else "synthetic",
            notice=self.real_notice if self.real_images else "합성 영상 · 실제 환자 영상이 아니에요(시험용)", version="series-v1",
            registered_by="registrar-000", registered_at="2026-10-15T00:00:00",
        )
        fields.update(over)
        return series_assets.SeriesAssets(**fields)

    def extra_series_row(self, case):
        """추가 합성 케이스의 열람 자산 등록 행(장 수는 케이스마다 다르다)."""
        series_assets = import_module("class.knee.series_assets")
        return series_assets.SeriesAssets(
            study_idx=case["study_idx"], manifest_version=case["manifest"].version, slice_set_sha256=case["manifest"].list_sha256,
            slice_sha256=tuple(hashlib.sha256(self.png_by_path[p]).hexdigest() for p in case["paths"]),
            slice_sizes=tuple((self.image_w, self.image_h) for _ in case["paths"]),
            kind="synthetic", notice="합성 영상 · 실제 환자 영상이 아니에요(시험용)", version="series-v1",
            registered_by="registrar-000", registered_at="2026-10-15T00:00:00",
        )

    def break_slice(self, index: int):
        """시험 전용: 디스크의 한 장을 손상시킨다(해시가 달라져 서버가 503 으로 닫는다)."""
        target = self.image_dir / self.paths[index].lstrip("/")
        self.broken.setdefault(index, target.read_bytes())
        target.write_bytes(b"corrupted-not-a-png")

    def fix_slice(self, index: int):
        data = self.broken.pop(index, None)
        if data is not None:
            (self.image_dir / self.paths[index].lstrip("/")).write_bytes(data)

    def swap_reference(self, rep: int, *, reference_id: int = 8, version: str = "v2", remove: bool = False):
        """승인표를 새 대표(다른 장·다른 영상 내용·새 기준 번호/버전)로 바꾼다. remove=True 면 승인을 철회한다."""
        if remove:
            slide_refs.APPROVED_SLIDE_REFERENCES.pop(self.study_idx, None)
            return None
        png = self.png_by_path[self.paths[rep]]
        ref = slide_refs.SlideReference(**{
            **self.ref.__dict__, "reference_id": reference_id, "version": version, "slice_no": rep,
            "image_path": self.paths[rep], "image_sha256": hashlib.sha256(png).hexdigest(),
        })
        self.images_by_ref_id[reference_id] = png
        slide_refs.APPROVED_SLIDE_REFERENCES[self.study_idx] = [ref]
        self.rep, self.rep_png, self.rep_sha, self.ref = rep, png, ref.image_sha256, ref
        return ref

    def reviews(self):
        with self.Session() as db:
            return db.query(ReviewNodeModel).order_by(ReviewNodeModel.idx).all()

    def close(self):
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main():
    parser = argparse.ArgumentParser(description="무릎 학습 화면 브라우저 시험용 서버(임시 DB, 시험 전용 로그인)")
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--no-approve", action="store_true", help="승인된 대표가 없는 상태로 연다")
    parser.add_argument("--no-series", action="store_true", help="여러 장 열람(합성 24장 서버 폴더 공급)을 끄고 이전처럼 대표 1장만 연다")
    parser.add_argument("--guest-origins", default="", help="게스트 실제 영상 열람 시험: 허용 프런트 Origin(쉼표, 예: http://127.0.0.1:8091). 주면 이 시험 서버에서만 게스트를 켠다")
    parser.add_argument("--extra-cases", default="", help="여러 케이스 무작위 시험: 추가 합성 케이스 장 수(쉼표, 예: 18,31). 첫 추가 케이스만 자기 기준을 가진다")
    args = parser.parse_args()
    import uvicorn

    extra = tuple(int(x) for x in args.extra_cases.split(",") if x.strip())
    with Harness(approve=not args.no_approve, series=not args.no_series, extra_cases=extra, guest_origins=args.guest_origins) as h:
        extra_text = ",".join("%d:%d:%s" % (c["study_idx"], c["count"], "ref" if c.get("ref") else "none") for c in h.extra) or "-"
        print(f"HARNESS_READY study={h.study_idx} unapproved={h.unapproved_study_idx} brain={h.brain_study_idx} "
              f"user={h.user_idx} other={h.other_user_idx} gone={h.gone_user_idx} login_idx={h.login_idx} "
              f"login_id={h.login_id} login_pw={h.login_pw} rep={h.rep} "
              f"ref={h.ref_box} size={h.image_w}x{h.image_h} series={'on' if h.series else 'off'} slices={len(h.paths)} "
              f"extra={extra_text}", flush=True)
        uvicorn.run(h.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
