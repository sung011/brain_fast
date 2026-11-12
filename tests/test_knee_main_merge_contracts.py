"""백엔드 main 병합(02e6df2 -> 22649b9) 뒤 무릎 계약 회귀 시험 — BACKEND_MAIN_KNEE_RECHECK_20261105 (MAIN-KNEE-001~004).

병합에서 들어온 팀원 변경이 아래 기존 계약을 깨지 않게 막는다. 실제 모델·프로젝트 DB·외부 저장소는 쓰지 않는다(가짜 DB, 가짜 저장, 함정 대역).

- 001 학습자 주소는 활성 학습자 세션만 인정한다. 폼 user_idx·관리자 세션·세션 없음으로 열리거나 다른 회원 명의로 저장되지 않는다.
- 002 기준 없음·토큰/영상 오류 등 미채점은 그대로 미채점이다. 모델 판독 스위치(KNEE_SLICE_READING_ENABLED)가 켜져 있어도
      판독 서비스·저장 서비스가 불리지 않고 graded=false·review_idx=null·저장 0 이다.
- 004 옛 한 장 API(screen-roi)는 빈 승인표에서 모델을 부르지 않고 DB 에 저장하지 않는다(KNEE_SCREEN_SAVE_ENABLED 가 켜져 있어도).
- 003 공용 저장 함수의 무릎 2차 가드는 임시 PostgreSQL 이 있어야 하는 tests/test_common_roi_knee_guard.py 가 맡는다. 여기서는 가드 코드 자체가
      저장 설정 확인보다 앞에 있는지 구조로 확인한다.

RouterSlideTests 의 모든 시험(로그인·이메일 승인·삭제 회원·미채점·저장 0)을 "병합 후 위험 설정"에서 다시 돌리는 방식이다.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import test_knee_slice as _slice   # noqa: E402  (클래스 이름을 가져오지 않아 같은 시험이 두 번 수집되지 않는다)

_TRAPPED = ("read_problem_slice", "save_screen_reading")


class HostileMergeSettingsRouterTests(_slice.RouterSlideTests):
    """병합 전 기본값이 아닌 설정(판독·저장 스위치 켜짐)에서도 RouterSlideTests 전체가 그대로 통과해야 한다."""

    def setUp(self):
        hostile = mock.patch.dict(os.environ, {"KNEE_SLICE_READING_ENABLED": "1", "KNEE_SCREEN_SAVE_ENABLED": "1"})
        hostile.start()
        self.addCleanup(hostile.stop)
        self.trapped_calls = []
        services = importlib.import_module("services.kneeScreenServices")
        for name in _TRAPPED:
            original = getattr(services, name, None)
            if original is None:
                continue

            # 라우터가 예외를 삼켜도 불린 사실이 남도록 호출을 기록한다(기록이 비어 있어야 통과).
            def make_trap(trap_name, is_async):
                if is_async:
                    async def trap(*args, **kwargs):
                        self.trapped_calls.append(trap_name)
                        raise AssertionError(f"{trap_name} 가 호출됐다")
                else:
                    def trap(*args, **kwargs):
                        self.trapped_calls.append(trap_name)
                        raise AssertionError(f"{trap_name} 가 호출됐다")
                return trap

            patcher = mock.patch.object(services, name, make_trap(name, inspect.iscoroutinefunction(original)))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: self.assertEqual(self.trapped_calls, [], "미채점/학습자 경로가 모델 판독·저장 서비스를 불렀다"))
        super().setUp()

    def test_form_user_idx_cannot_replace_a_login(self):
        self.approve(self.ref)
        res = self.post({"user_idx": "77", "slice_index": "12", "problem_token": self.real_token(user_idx=77)})
        self.assertEqual(res.status_code, 401, res.text)          # 폼 번호는 회원 식별로 쓰지 않는다
        self.assertNothingSaved()

    def test_form_user_idx_cannot_be_used_as_the_recorded_user(self):
        token = self.ready()                                       # 5번 회원으로 로그인
        res = self.post({"user_idx": "77", "slice_index": "12", "problem_token": token})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(self.created)
        self.assertTrue(all(row["rn_u_idx"] == 5 for row in self.created), self.created)    # 77 이 아니라 세션의 5

    def test_unauthenticated_learner_doors_are_closed_for_task(self):
        self.approve(self.ref)
        self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": 3}).status_code, 401)

    def test_no_reference_and_token_failures_stay_ungraded_without_reading(self):
        self.login()                                               # 승인표 없음 -> 기준 없음
        body = self.post({"slice_index": "12"}).json()
        self.assertEqual((body["graded"], body["review_idx"], body["result"]), (False, None, None))
        self.assertEqual(body["not_graded_reason"], "no_reference_for_slice")
        self.assertNothingSaved()
        self.approve(self.ref)                                     # 승인표 있음 + 토큰 없음/엉터리
        for token in ("", "garbage"):
            body = self.post({"slice_index": "12", "problem_token": token}).json()
            self.assertFalse(body["graded"], body)
            self.assertIsNone(body["review_idx"], body)
        self.assertNothingSaved()


class StructureTests(unittest.TestCase):
    def test_learner_router_does_not_wire_the_model_reading_path(self):
        source = (ROOT / "routers" / "kneeLearning.py").read_text(encoding="utf-8")
        for banned in ("kneeScreenServices", "read_problem_slice", "KNEE_SLICE_READING_ENABLED"):
            self.assertNotIn(banned, source, banned)

    def test_learner_idx_has_no_request_supplied_user(self):
        tree = ast.parse((ROOT / "routers" / "kneeLearning.py").read_text(encoding="utf-8"))
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_learner_idx")
        self.assertEqual([a.arg for a in fn.args.args], ["request", "db"])
        grade = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "learning_roi_grade_knee")
        self.assertNotIn("user_idx", [a.arg for a in grade.args.args + grade.args.kwonlyargs])

    def test_common_save_guard_precedes_storage_and_upload(self):
        source = (ROOT / "services" / "reviewNodeServices.py").read_text(encoding="utf-8")
        body = source[source.index("async def save_submission"):]
        guard, enabled, upload, create = (body.index(s) for s in ('== "4"', "storage_service.enabled", "upload_bytes", "repositories.create"))
        self.assertLess(guard, enabled)
        self.assertLess(guard, upload)
        self.assertLess(guard, create)
        self.assertIn("db.no_autoflush", body[:guard])
        self.assertIn("select(StudyModel.st_part)", body[:guard])
        self.assertIn("image_saved", body)                          # 팀원이 더한 '업로드 실패해도 기록 유지' 동작은 그대로

    def test_screen_roi_route_does_not_save(self):
        source = (ROOT / "routers" / "knee.py").read_text(encoding="utf-8")
        for banned in ("kneeScreenServices", "SessionLocal", "review_node", "KNEE_SCREEN_SAVE_ENABLED"):
            self.assertNotIn(banned, source, banned)


class ScreenRoiDefaultContractTests(unittest.TestCase):
    """옛 한 장 API: 승인표가 비어 있으면 모델을 부르지 않고 저장하지 않는다(저장 스위치가 켜져 있어도)."""

    def test_empty_gate_never_predicts_and_never_saves(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from PIL import Image
        import io

        gates = importlib.import_module("class.knee.gates")
        screen_roi = importlib.import_module("class.knee.screen_roi")
        knee_router = importlib.import_module("routers.knee").router
        services = importlib.import_module("services.kneeScreenServices")
        calls = {"predict": 0}

        def fake_predict(finding, plane, gray):
            calls["predict"] += 1
            return 0.7

        import numpy as np
        buf = io.BytesIO()
        ramp = (np.add.outer(np.arange(200), np.arange(200)) % 256).astype("uint8")     # 한 가지 밝기뿐인 그림은 blank_image 로 거절된다
        Image.fromarray(ramp, mode="L").save(buf, format="PNG")
        app = FastAPI()
        app.include_router(knee_router, prefix="/api/v1")
        fields = [("body_part", (None, "knee")), ("plane", (None, "sagittal")), ("image_scope", (None, "full_slice"))]
        with mock.patch.dict(os.environ, {"KNEE_SCREEN_SAVE_ENABLED": "1"}), \
                mock.patch.object(gates, "APPROVED", {}), mock.patch.object(gates, "APPROVED_LOCATION", {}), \
                mock.patch.object(screen_roi, "_predict_single", fake_predict), \
                mock.patch.object(services, "save_screen_reading", side_effect=AssertionError("옛 한 장 API 가 저장을 불렀다")):
            res = TestClient(app).post("/api/v1/knee/screen-roi", files=[("image", ("a.png", buf.getvalue(), "image/png"))] + fields)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["analysis_status"], "not_evaluated")
        self.assertEqual(calls["predict"], 0)
        self.assertNotIn("review_idx", body)
        self.assertEqual(body["evidence"]["validated"], [])


if __name__ == "__main__":
    unittest.main()
