"""
공용 ROI 채점(POST /learning/roi-grade)·저장 함수(save_submission)의 무릎 차단 가드 시험 (MSN-20260930-001, COMMON_ROI_KNEE_GUARD_PROPOSAL r2).

- 서버 DB study.st_part 가 '4'(무릎)이면 공용 주소는 계산·저장 없이 고정 400, 저장 함수는 고정 ValueError.
- 판단은 요청 값(st_part·user_idx·slice)이 아니라 **지금 DB 값을 새로 SELECT** 한 결과다(같은 세션 캐시 재사용 금지).
- 임시 내장 PostgreSQL(pgserver) 한 개만 쓴다. 프로젝트 DB·.env·NAS/NCP·HF·모델은 쓰지 않는다.
  채점 모델·Object Storage 업로드·SSE 는 가짜로 바꾸고, 실제 공용 라우트·가드·저장 함수·임시 DB 저장은 그대로 지난다.
- 한계: 함수 직접 호출이 아니라 TestClient 경로에서는 multipart 본문이 라우트 전에 파싱·임시 저장된다(UploadFile.read 0 은 라우트 안 읽기 기준).
실행: python -m pytest tests/test_common_roi_knee_guard.py   (pgserver·psycopg 필요, 없으면 건너뜀. boto3 는 requirements 에 있음)
다른 모듈(수정 전 원본·변형 시험)에도 같은 시나리오를 돌릴 수 있게 helper 는 (commonness 모듈, reviewNodeServices 모듈)을 인자로 받는다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile as StarletteUploadFile

from db import Base, get_db
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from routers import commonness as COMMONNESS
from schemas.userSchemas import UserCreate
from services import learningServices, roiGradeServices
from services import reviewNodeServices as RNS
from services import userServices as user_service
from services.objectStorageServices import ObjectStorageService, storage_service
from utils.review_sse import review_events

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False

MSG = "무릎 문제는 이 주소로 채점하지 않습니다. POST /learning/roi-grade/knee 를 사용하세요."
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-not-a-real-image"          # 합성 바이트(채점 모델이 가짜라 해석하지 않음)
FAKE_GRADE = {"result": "correct", "recall": 0.9, "iou": 0.8, "overlay_summary": "합성", "overlay_png_base64": None, "_review_png": b"\x89PNG-review"}
EXPECTED_KEYS = {"result", "recall", "iou", "overlay_summary", "overlay_png_base64", "review_idx", "rn_image", "rn_solution", "remote_path", "next"}


class Env:
    """임시 PostgreSQL + 합성 문제 행. 실제 DB 와 무관하고 close() 때 지운다."""

    def __init__(self):
        import pgserver
        self._stack = contextlib.ExitStack()
        self.tmp = Path(tempfile.mkdtemp(prefix="common_guard_pg_"))
        self._stack.callback(shutil.rmtree, self.tmp, ignore_errors=True)
        server = pgserver.get_server(self.tmp / "pg", cleanup_mode="stop")
        self._stack.callback(server.cleanup)
        uri = server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_engine(uri)
        self._stack.callback(self.engine.dispose)
        Base.metadata.create_all(self.engine, tables=[UserModel.__table__, StudyModel.__table__, ReviewNodeModel.__table__])
        self.Session = sessionmaker(bind=self.engine, autoflush=False)          # 제품 SessionLocal 과 같은 autoflush=False
        with self.Session() as db:
            body = UserCreate(user_id="guard-a@example.test", user_pw="synthetic-pass-1234", user_name="합성", user_birth="2000-01-01", user_un="합성대", user_sp="합성과")
            self.user_idx = user_service.create_user(db, body).idx
            body2 = UserCreate(user_id="guard-b@example.test", user_pw="synthetic-pass-1234", user_name="합성", user_birth="2000-01-01", user_un="합성대", user_sp="합성과")
            self.other_user_idx = user_service.create_user(db, body2).idx

            def add(part, modal, image, del_yn="N", disease="합성"):
                row = StudyModel(st_part=part, st_modal=modal, st_image=image, del_yn=del_yn, st_disease=disease)
                db.add(row); db.flush()
                return row.idx
            slide = lambda name: json.dumps([f"/stylesheets/assets/{name}/{i:02d}.png" for i in range(3)])
            self.ids = {
                "knee_single": add("4", "3", "/stylesheets/assets/knee/one.png"),
                "knee_slide": add("4", "3", slide("knee")),
                "knee_deleted": add("4", "3", "/stylesheets/assets/knee/gone.png", del_yn="Y"),
                "brain_ct": add("1", "2", "/stylesheets/assets/brain/ct.png"),
                "brain_mri": add("1", "3", slide("brain")),
                "chest": add("2", "1", "/stylesheets/assets/chest/x.png"),
                "abdomen": add("3", "2", "/stylesheets/assets/abdomen/ct.png"),
                "race_brain": add("1", "2", "/stylesheets/assets/brain/race.png"),
            }
            # 다음 문제(next)가 생기도록 같은 부위·종류 한 개씩 더
            for part, modal, name in (("1", "2", "brain/ct2"), ("1", "3", "brain/mri2"), ("2", "1", "chest/x2"), ("3", "2", "abdomen/ct2")):
                add(part, modal, f"/stylesheets/assets/{name}.png" if modal != "3" else slide(name))
            db.commit()
        self.missing_idx = 999999

    def reviews(self):
        with self.Session() as db:
            return db.execute(select(func.count()).select_from(ReviewNodeModel)).scalar_one()

    def set_part(self, idx, part):
        with self.Session() as db:
            db.execute(update(StudyModel).where(StudyModel.idx == idx).values(st_part=part)); db.commit()

    def close(self):
        self._stack.close()


def make_app(env, commonness_mod=COMMONNESS, preload_idx=None):
    """공용 라우터만 올린 시험 앱. preload_idx 를 주면 요청 세션이 그 행을 미리 읽어 둔다(identity map 에 옛 값)."""
    app = FastAPI()
    app.include_router(commonness_mod.router)

    def session():
        with env.Session() as db:
            held = []                                                       # identity map 은 약한 참조 — 요청 내내 붙잡아 둬야 '미리 읽은 옛 값'이 남는다
            if preload_idx is not None:
                held.append(db.get(StudyModel, preload_idx))
            yield db
            held.clear()

    app.dependency_overrides[get_db] = session
    return TestClient(app, raise_server_exceptions=False)


@contextlib.contextmanager
def probes(grade_hook=None):
    """호출 횟수 측정: resolver·UploadFile.read·채점 모델·저장 설정 확인·업로드·SSE·다음 문제. 모델·업로드·SSE 는 가짜."""
    c = {"resolve": 0, "read": 0, "grade": 0, "enabled": 0, "upload": 0, "sse": 0, "next": 0}
    real_resolve, real_next, real_read = learningServices.resolve_roi_grade_slice, learningServices.get_random_problem, StarletteUploadFile.read

    def resolve(*a, **k):
        c["resolve"] += 1
        return real_resolve(*a, **k)

    def next_problem(*a, **k):
        c["next"] += 1
        return real_next(*a, **k)

    async def read(self, *a, **k):
        c["read"] += 1
        return await real_read(self, *a, **k)

    def grade(*a, **k):
        c["grade"] += 1
        if grade_hook:
            grade_hook()
        return dict(FAKE_GRADE)

    def enabled(self):
        c["enabled"] += 1
        return True

    async def upload(content, name, remote_dir=None):
        c["upload"] += 1
        return {"remote_path": f"{remote_dir}/{name}"}

    async def publish(event, data=None):
        c["sse"] += 1

    with mock.patch.object(learningServices, "resolve_roi_grade_slice", resolve), \
            mock.patch.object(learningServices, "get_random_problem", next_problem), \
            mock.patch.object(StarletteUploadFile, "read", read), \
            mock.patch.object(roiGradeServices, "grade_image_roi", grade), \
            mock.patch.object(ObjectStorageService, "enabled", property(enabled)), \
            mock.patch.object(storage_service, "upload_bytes", upload), \
            mock.patch.object(storage_service, "to_public_path", lambda p: p), \
            mock.patch.object(review_events, "publish", publish):
        yield c


def post(client, study_idx=None, **extra):
    data = {"roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"}
    if study_idx is not None:
        data["study_idx"] = str(study_idx)
    data.update({k: str(v) for k, v in extra.items()})
    return client.post("/learning/roi-grade", data=data, files={"image": ("s.png", PNG, "image/png")})


def run_case(env, study_idx, commonness_mod=COMMONNESS, preload_idx=None, grade_hook=None, **extra):
    """한 번 제출하고 (상태, 본문, 호출 횟수, review 행 증가)를 돌려준다."""
    client = make_app(env, commonness_mod, preload_idx)
    before = env.reviews()
    with probes(grade_hook) as c:
        res = post(client, study_idx, **extra)
    body = res.json() if res.headers.get("content-type", "").startswith("application/json") else res.text
    return res.status_code, body, dict(c), env.reviews() - before


def run_save_direct(env, study_idx, rns_mod=RNS, storage_obj=None):
    """save_submission 직접 호출. storage_obj 를 주면 그 객체를 저장 장치로 끼운다(설정 확인 전에 가드가 서는지)."""
    before = env.reviews()
    with probes() as c, env.Session() as db:
        patcher = mock.patch.object(rns_mod, "storage_service", storage_obj) if storage_obj is not None else contextlib.nullcontext()
        with patcher:
            try:
                out = asyncio.run(rns_mod.save_submission(db, image_bytes=PNG, image_filename="s.png", grade=dict(FAKE_GRADE), study_idx=study_idx, user_idx=env.user_idx))
                err = None
            except Exception as exc:  # noqa: BLE001
                out, err = None, exc
    return out, err, dict(c), env.reviews() - before


def race_case(env, commonness_mod=COMMONNESS):
    """AC4: 첫 세션이 뇌 행을 미리 읽음 → 모델 대역 중 두 번째 세션(별도 연결)이 st_part=4 로 commit → 2차 가드가 최신 SELECT 로 400."""
    idx = env.ids["race_brain"]
    env.set_part(idx, "1")
    hook_state = {}

    def change_in_other_session():
        def worker():
            with env.Session() as other:
                other.execute(update(StudyModel).where(StudyModel.idx == idx).values(st_part="4"))
                other.commit()
                hook_state["committed"] = True
        t = threading.Thread(target=worker); t.start(); t.join(10)

    try:
        return run_case(env, idx, commonness_mod, preload_idx=idx, grade_hook=change_in_other_session) + (hook_state.get("committed", False),)
    finally:
        env.set_part(idx, "1")


ZERO_AFTER_GUARD = {"resolve": 0, "read": 0, "grade": 0, "enabled": 0, "upload": 0, "sse": 0, "next": 0}


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg 가 없어 임시 DB 시험을 건너뜀")
class CommonRoiKneeGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def assert_blocked(self, out):
        status, body, calls, inserted = out
        self.assertEqual(status, 400, body)
        self.assertEqual(body, {"detail": MSG})
        self.assertEqual(calls, ZERO_AFTER_GUARD)
        self.assertEqual(inserted, 0)

    # AC1 ------------------------------------------------------------------
    def test_ac1_db_knee_single_and_slide_blocked_before_any_work(self):
        for key, extra in (("knee_single", {}), ("knee_slide", {"slice_index": 1})):
            with self.subTest(key):
                self.assert_blocked(run_case(self.env, self.env.ids[key], user_idx=self.env.user_idx, **extra))

    def test_ac1_independent_of_switches_and_login_like_inputs(self):
        # 공용 주소에는 로그인·승인·채점 스위치가 없다. 무릎 채점 스위치 환경값을 켜도/꺼도 같은 400.
        for flag in ("1", "0"):
            with self.subTest(flag=flag), mock.patch.dict("os.environ", {"KNEE_ROI_GRADE_ENABLED": flag}):
                self.assert_blocked(run_case(self.env, self.env.ids["knee_single"]))

    # AC2 ------------------------------------------------------------------
    def test_ac2_request_values_cannot_flip_the_decision(self):
        knee, brain = self.env.ids["knee_single"], self.env.ids["brain_ct"]
        self.assert_blocked(run_case(self.env, knee, st_part="1", st_modal="2"))                       # 요청은 뇌라고 해도 DB 무릎 → 400
        self.assert_blocked(run_case(self.env, knee, user_idx=self.env.other_user_idx, slice_index=0, image_path="/stylesheets/assets/brain/ct.png"))
        self.assert_blocked(run_case(self.env, self.env.ids["knee_slide"], slice_index=99))            # 슬라이스 조작도 가드가 먼저
        status, body, calls, inserted = run_case(self.env, brain, st_part="4")                          # 요청이 무릎이라 해도 DB 뇌 → 기존 경로
        self.assertEqual(status, 200, body); self.assertEqual(inserted, 1); self.assertEqual(calls["upload"], 1)

    def test_ac2_soft_deleted_knee_still_blocked(self):
        self.assert_blocked(run_case(self.env, self.env.ids["knee_deleted"]))

    # AC3 ------------------------------------------------------------------
    def test_ac3_save_submission_direct_knee_raises_before_storage(self):
        class NoStorage:                                                                                # 설정 속성조차 없음 → 접근하면 AttributeError
            pass
        for key in ("knee_single", "knee_slide", "knee_deleted"):
            with self.subTest(key):
                out, err, calls, inserted = run_save_direct(self.env, self.env.ids[key], storage_obj=NoStorage())
                self.assertIsInstance(err, ValueError); self.assertEqual(str(err), MSG)
                self.assertEqual((calls["enabled"], calls["upload"], inserted), (0, 0, 0))

    def test_ac3_save_submission_direct_other_parts_unchanged(self):
        for key in ("brain_ct", "brain_mri", "chest", "abdomen"):
            with self.subTest(key):
                out, err, calls, inserted = run_save_direct(self.env, self.env.ids[key])
                self.assertIsNone(err, err)
                self.assertLessEqual({"review_idx", "rn_image", "rn_solution", "remote_path"}, set(out))   # 기존 키 유지(팀 병합으로 키가 늘어도 깨지지 않게)
                self.assertEqual((calls["enabled"], calls["upload"], inserted), (1, 1, 1))
        out, err, calls, inserted = run_save_direct(self.env, None)                                    # 번호 없음 = 기존 경로
        self.assertIsNone(err); self.assertEqual(inserted, 1)

    # AC4 ------------------------------------------------------------------
    def test_ac4_part_changed_by_second_session_mid_request_is_caught_by_fresh_select(self):
        status, body, calls, inserted, committed = race_case(self.env)
        self.assertTrue(committed, "두 번째 세션 변경이 commit 되지 않음")
        self.assertEqual(status, 400, body); self.assertEqual(body, {"detail": MSG})
        self.assertEqual(calls["grade"], 1)                                                            # 이미 시작한 모델 1회는 허용
        self.assertEqual((calls["enabled"], calls["upload"], calls["sse"], calls["next"], inserted), (0, 0, 0, 0, 0))

    def test_ac4_session_really_holds_the_stale_cached_row(self):
        # 위 경쟁 시험의 전제 확인: 같은 세션의 db.get 은 다른 세션 commit 뒤에도 옛 값('1')을 돌려준다(그래서 2차 가드는 새 SELECT 가 필요).
        idx = self.env.ids["race_brain"]
        self.env.set_part(idx, "1")
        try:
            with self.env.Session() as db:
                held = db.get(StudyModel, idx)
                self.env.set_part(idx, "4")
                self.assertEqual(db.get(StudyModel, idx).st_part, "1")
                self.assertEqual(db.execute(select(StudyModel.st_part).where(StudyModel.idx == idx)).scalar_one(), "4")
                del held
        finally:
            self.env.set_part(idx, "1")

    def test_guard_select_does_not_flush_pending_changes(self):
        # 자동 flush 가 켜진 세션이라도 가드의 검사 SELECT 가 보류 중 변경을 DB 에 쓰지 않는다(no_autoflush).
        idx = self.env.ids["knee_single"]
        Flushing = sessionmaker(bind=self.env.engine, autoflush=True)
        with Flushing() as db:
            row = db.get(StudyModel, int(self.env.ids["brain_ct"]))
            row.st_disease = "보류중변경"                                                                # 아직 flush 안 한 변경
            with self.assertRaises(ValueError):
                asyncio.run(RNS.save_submission(db, image_bytes=PNG, image_filename="s.png", grade=dict(FAKE_GRADE), study_idx=idx))
            self.assertIn(row, db.dirty)                                                                 # 여전히 보류 상태
            with self.env.Session() as other:
                self.assertNotEqual(other.get(StudyModel, int(self.env.ids["brain_ct"])).st_disease, "보류중변경")
            db.rollback()

    # AC5 ------------------------------------------------------------------
    def test_ac5_normal_parts_keep_existing_path(self):
        ids = self.env.ids
        for key, extra in (("brain_ct", {}), ("brain_mri", {"slice_index": 1}), ("chest", {}), ("abdomen", {})):
            with self.subTest(key):
                status, body, calls, inserted = run_case(self.env, ids[key], user_idx=self.env.user_idx, **extra)
                self.assertEqual(status, 200, body)
                self.assertLessEqual(EXPECTED_KEYS, set(body))   # 기존 응답 키 유지(정확 일치는 수정 전/후 비교 스크립트로 확인)
                self.assertIsNotNone(body["next"])
                self.assertEqual((calls["resolve"], calls["read"], calls["grade"], calls["enabled"], calls["upload"], calls["sse"], inserted), (1, 1, 1, 1, 1, 1, 1))
        for idx in (None, self.env.missing_idx):                                                       # 번호 없음·없는 번호 = 기존 경로
            with self.subTest(idx=idx):
                status, body, calls, inserted = run_case(self.env, idx)
                self.assertEqual(status, 200, body); self.assertEqual(inserted, 1); self.assertEqual(calls["upload"], 1)

    def test_ac5_brain_slide_missing_slice_keeps_existing_400(self):
        status, body, calls, inserted = run_case(self.env, self.env.ids["brain_mri"])
        self.assertEqual(status, 400); self.assertIn("slice_index", body["detail"])
        self.assertEqual((calls["grade"], inserted), (0, 0))

    def test_ac5_knee_dedicated_route_is_not_touched_by_this_guard(self):
        # 무릎 전용 라우트는 commonness 가 아니라 kneeLearning 에 있다 — 공용 라우터에는 /learning/roi-grade/knee 가 없어야 한다.
        paths = {r.path for r in COMMONNESS.router.routes}
        self.assertIn("/learning/roi-grade", paths)
        self.assertNotIn("/learning/roi-grade/knee", paths)


if __name__ == "__main__":
    unittest.main()
