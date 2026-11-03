"""
무릎 학습 ROI 채점 API(POST /learning/roi-grade/knee) 테스트.

DB·이벤트는 가짜로 바꿔 끼우고, 인증·문제 결속·IoU 채점·기준 ROI 승인 규칙은 실제 코드를 쓴다.
실행: python -m pytest tests/test_knee_learning.py
"""
import os
import sys
import unittest
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from db import get_db
from routers import kneeLearning
from services import kneeRoiGradeServices as grade

refs = import_module("class.knee.reference_rois")

STUDY = SimpleNamespace(idx=3, del_yn="N", st_part="4", st_modal="3", st_image="/img/knee3.png", st_disease="ACL 파열")
REF = dict(study_idx=3, plane="sagittal", x=0.3, y=0.3, width=0.4, height=0.4, source="KneeMRI 라벨 v1",
           version="v1", reviewed_by="검수자A", reviewed_at="2026-10-01")


def approved(**over):
    return refs.ReferenceRoi(**{**REF, **over})


class Base(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")

        @app.post("/_login")
        async def _login(request: Request, idx: int = 5, verified: bool = True):
            request.session["learner"] = {"idx": idx, "email_verified": verified}   # 학습자 세션 키는 learner (관리자 user 와 분리)
            return {"ok": True}

        app.include_router(kneeLearning.router)
        app.dependency_overrides[get_db] = lambda: object()
        self.client = TestClient(app, raise_server_exceptions=False)

        self.created = []
        self.published = []
        self.study = STUDY
        self.gone_users = set()

        def fake_create(db, **kw):
            self.created.append(kw)
            return SimpleNamespace(idx=11, rn_image=kw["rn_image"], rn_solution=kw["rn_solution"])

        async def fake_publish(name, payload):
            self.published.append((name, payload))

        self.next_row = SimpleNamespace(idx=99, st_part="4", st_modal="3", st_image="/img/99.png")
        self.next_args = []

        def fake_next(db, **kw):
            self.next_args.append(kw)
            return self.next_row

        env = mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": "1", "KNEE_SLICE_READING_ENABLED": "0"})
        env.start()
        self.addCleanup(env.stop)
        patches = [
            mock.patch.object(kneeLearning.review_node_repositories, "create", fake_create),
            mock.patch.object(kneeLearning.review_events, "publish", fake_publish),
            mock.patch.object(kneeLearning.study_repositories, "get_by_idx", lambda db, idx: self.study if idx == 3 else None),
            mock.patch.object(kneeLearning.learning_service, "get_random_problem", fake_next),
            mock.patch.object(kneeLearning.user_service, "get_user", lambda db, idx: None if idx in self.gone_users else SimpleNamespace(idx=idx)),
            mock.patch.dict(refs.APPROVED_REFERENCES, {}, clear=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def login(self, idx=5, verified=True):
        self.assertEqual(self.client.post("/_login", params={"idx": idx, "verified": str(verified).lower()}).status_code, 200)

    def approve(self, **over):
        refs.APPROVED_REFERENCES[3] = approved(**over)

    def post(self, data=None, **extra):
        form = {"study_idx": "3", "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"}
        form.update(data or {})
        return self.client.post("/learning/roi-grade/knee", data=form, **extra)

    def ready(self, **over):
        """로그인 + 승인된 기준 ROI 를 갖춘 정상 상태."""
        self.login()
        self.approve(**over)


class AccessControlTests(Base):
    def test_disabled_by_default(self):
        self.ready()
        with mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": ""}):
            with mock.patch.object(import_module("class.knee.config"), "_read_dotenv", lambda key: ""):
                res = self.post()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(self.created, [])

    def test_grade_without_login_is_allowed(self):
        self.approve()
        res = self.post()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.created[0]["rn_u_idx"], None)

    def test_unverified_session_still_grades(self):
        self.login(verified=False)
        self.approve()
        res = self.post()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.created[0]["rn_u_idx"], 5)

    def test_bad_session_values_are_rejected(self):
        self.approve()
        # 세션에 idx 가 없거나 숫자가 아니거나 양수가 아니면 로그인으로 보지 않는다
        for session_user in ({}, {"idx": "5", "email_verified": True}, {"idx": True, "email_verified": True},
                             {"idx": 0, "email_verified": True}, {"idx": -3, "email_verified": True},
                             {"idx": 5.0, "email_verified": True}, {"idx": None, "email_verified": True}):
            with self.subTest(user=session_user):
                app = FastAPI()
                app.add_middleware(SessionMiddleware, secret_key="s")

                @app.post("/_set")
                async def _set(request: Request, _u=session_user):
                    request.session["learner"] = _u
                    return {}

                app.include_router(kneeLearning.router)
                app.dependency_overrides[get_db] = lambda: object()
                client = TestClient(app, raise_server_exceptions=False)
                client.post("/_set")
                res = client.post("/learning/roi-grade/knee", data={"study_idx": "3", "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"})
                self.assertEqual(res.status_code, 200, res.text)

    def test_admin_session_counts_as_login(self):
        """관리자 로그인(session["user"])도 같은 활성 회원의 로그인으로 본다."""
        self.approve()
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="s")

        @app.post("/_admin")
        async def _admin(request: Request):
            request.session["user"] = {"idx": 5, "user_id": "admin", "mb_level": 9, "email_verified": True}
            return {}

        app.include_router(kneeLearning.router)
        app.dependency_overrides[get_db] = lambda: object()
        client = TestClient(app, raise_server_exceptions=False)
        client.post("/_admin")
        res = client.post("/learning/roi-grade/knee", data={"study_idx": "3", "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.created[0]["rn_u_idx"], 5)

    def test_missing_user_does_not_block_grade(self):
        """세션 번호가 DB 에 없어도 채점은 진행하고, 기록에는 회원 번호를 넣지 않는다."""
        self.gone_users = {5}
        self.ready()
        res = self.post()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.created[0]["rn_u_idx"], None)

    def test_user_comes_from_session_not_request(self):
        self.login(idx=42)
        self.approve()
        res = self.post({"user_idx": "7", "rn_u_idx": "8"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.created[0]["rn_u_idx"], 42)
        self.assertEqual(self.published[0][1]["user_idx"], 42)
        self.assertEqual(self.next_args[0]["user_idx"], 42)


class StudyBindingTests(Base):
    def test_unknown_or_ineligible_study_is_404(self):
        self.ready()
        for study in (None, SimpleNamespace(**{**STUDY.__dict__, "st_part": "1"}), SimpleNamespace(**{**STUDY.__dict__, "st_modal": "2"}),
                      SimpleNamespace(**{**STUDY.__dict__, "del_yn": "Y"})):
            with self.subTest(study=study):
                self.study = study
                res = self.post({"study_idx": "3" if study is not None else "4"})
                self.assertEqual(res.status_code, 404, res.text)
        self.assertEqual(self.created, [])

    def test_study_idx_is_required(self):
        self.ready()
        form = {"roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"}
        self.assertEqual(self.client.post("/learning/roi-grade/knee", data=form).status_code, 422)

    def test_uploaded_image_is_ignored_and_never_stored(self):
        self.ready()
        res = self.client.post(
            "/learning/roi-grade/knee",
            data={"study_idx": "3", "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"},
            files={"image": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.created[0]["rn_image"], "/img/knee3.png")  # 서버가 가진 문제 이미지 경로
        self.assertNotIn("image", " ".join(self.created[0].keys()).replace("rn_image", ""))

    def test_plane_is_not_taken_from_request(self):
        self.ready(plane="coronal")
        body = self.post({"plane": "axial"}).json()
        self.assertEqual(body["plane"], "coronal")  # 서버 기록(기준 ROI)의 촬영면


class ReferenceApprovalTests(Base):
    def test_no_approved_reference_means_not_graded_and_nothing_saved(self):
        self.login()
        body = self.post().json()
        self.assertFalse(body["graded"])
        self.assertEqual(body["not_graded_reason"], "reference_not_approved")
        self.assertIsNone(body["result"])
        self.assertIsNone(body["iou"])
        self.assertIsNone(body["reference_bbox"])
        self.assertEqual(self.created, [])
        self.assertEqual(self.published, [])

    def test_incomplete_approvals_are_ignored(self):
        self.login()
        for over in ({"reviewed_by": ""}, {"reviewed_at": " "}, {"source": ""}, {"version": ""}, {"plane": "oblique"},
                     {"coord_system": "pixels"}, {"width": 0.0}, {"x": 0.8, "width": 0.4}, {"x": float("nan")}, {"study_idx": 4}):
            with self.subTest(over=over):
                self.approve(**over)
                self.assertFalse(self.post().json()["graded"])
        self.assertEqual(self.created, [])

    def test_next_problem_is_still_offered_when_not_graded(self):
        self.login()
        body = self.post().json()
        self.assertEqual(body["next"], {"idx": 99, "st_part": "4", "st_modal": "3", "st_image": "/img/99.png"})
        self.assertEqual(self.next_args[0]["st_part"], "4")
        self.assertEqual(self.next_args[0]["st_modal"], "3")
        self.assertEqual(self.next_args[0]["exclude_idxs"], [3])

    def test_no_model_is_used_for_grading(self):
        source = (Path(__file__).resolve().parents[1] / "routers" / "kneeLearning.py").read_text(encoding="utf-8")
        service = (Path(__file__).resolve().parents[1] / "services" / "kneeRoiGradeServices.py").read_text(encoding="utf-8")
        for text in (source, service):
            for banned in ("locate_acl", "screen_roi", "YOLO(", "import torch", "nas_service", "upload_bytes", "UploadFile"):
                self.assertNotIn(banned, text)


class IouRuleTests(Base):
    """D-069: IoU≥0.5 정답, 0.2≤IoU<0.5 부분정답, 그 미만 오답."""

    def test_verdict_boundaries(self):
        """판정은 정수 픽셀 수로만 한다(실수 IoU 를 반올림해 비교하지 않는다)."""
        cases = [((1, 2), "정답"), ((125000, 250000), "정답"), ((124999, 250000), "부분정답"), ((124990, 250000), "부분정답"),
                 ((1, 5), "부분정답"), ((50000, 250000), "부분정답"), ((49999, 250000), "오답"), ((0, 100), "오답"),
                 ((100, 100), "정답")]
        for (inter, union), expected in cases:
            with self.subTest(inter=inter, union=union):
                self.assertEqual(grade.verdict_from_px(inter, union), expected)

    def test_verdict_rejects_impossible_counts(self):
        for inter, union in ((0, 0), (1, 0), (5, 4), (-1, 10), (1.5, 3), (1, 3.0), (True, 2)):
            with self.subTest(inter=inter, union=union):
                with self.assertRaises(ValueError):
                    grade.verdict_from_px(inter, union)

    def test_exact_box_is_correct_and_saved(self):
        self.ready()
        body = self.post().json()
        self.assertTrue(body["graded"])
        self.assertEqual(body["result"], "정답")
        self.assertGreater(body["iou"], 0.99)
        self.assertEqual(self.created[0]["rn_solution"], "C")
        self.assertEqual(self.created[0]["rn_s_idx"], 3)
        self.assertEqual(self.created[0]["rn_disease"], "ACL 파열")
        self.assertEqual(body["review_idx"], 11)
        self.assertEqual(self.published[0][0], "review_created")

    def test_partial_and_wrong(self):
        self.ready()
        # 기준 0.3~0.7 정사각형. 왼쪽 반만 겹치는 박스: 교집합 0.2×0.4, 합집합 0.16+0.16-0.08=0.24 → IoU 0.333
        partial = self.post({"x": "0.1", "y": "0.3", "width": "0.4", "height": "0.4"}).json()
        self.assertEqual(partial["result"], "부분정답")
        self.assertEqual(self.created[-1]["rn_solution"], "H")
        wrong = self.post({"x": "0.0", "y": "0.0", "width": "0.1", "height": "0.1"}).json()
        self.assertEqual(wrong["result"], "오답")
        self.assertEqual(self.created[-1]["rn_solution"], "W")

    def test_full_screen_roi_on_small_reference_is_wrong(self):
        """Codex 재현: 화면 전체 ROI 와 작은 기준 ROI(재현율 1.0, IoU≈0.04)는 오답이어야 한다."""
        self.ready(x=0.4, y=0.4, width=0.2, height=0.2)
        body = self.post({"x": "0", "y": "0", "width": "1", "height": "1"}).json()
        self.assertEqual(body["result"], "오답")
        self.assertLess(body["iou"], 0.06)
        self.assertEqual(self.created[0]["rn_solution"], "W")

    def test_large_roi_over_half_of_screen_stays_wrong_when_iou_low(self):
        self.ready(x=0.4, y=0.4, width=0.2, height=0.2)
        body = self.post({"x": "0.1", "y": "0.1", "width": "0.8", "height": "0.8"}).json()
        self.assertEqual(body["result"], "오답")  # IoU 0.0625

    def test_rule_version_recorded(self):
        self.ready()
        rule = self.post().json()["rule"]
        self.assertEqual(rule, {"metric": "iou", "version": "D-069/2026-09-16", "correct_iou": 0.5, "partial_iou": 0.2})


PX = 1 / 512  # 채점 격자 한 칸. 2 의 거듭제곱이라 실수로 정확히 표현되어 픽셀 수를 손으로 계산할 수 있다


def px_box(x, y, w, h):
    return {"x": str(x * PX), "y": str(y * PX), "width": str(w * PX), "height": str(h * PX)}


class IntegerScoringTests(Base):
    """7차 검토 후속: 판정의 원본은 정수 픽셀 수이고 실수 IoU 는 표시용이다(DDL 의 inter_px/union_px 와 같은 값)."""

    def approve_px(self, x, y, w, h):
        self.approve(x=x * PX, y=y * PX, width=w * PX, height=h * PX)

    def test_exactly_half_overlap_is_correct(self):
        self.login()
        self.approve_px(0, 0, 5, 4)                                  # 기준 20픽셀
        body = self.post(px_box(0, 0, 5, 2)).json()                  # 사용자 10픽셀, 모두 기준 안
        self.assertEqual(body["overlap_px"], {"grid_size": 512, "intersection": 10, "union": 20})
        self.assertEqual(body["result"], "정답")
        self.assertEqual(body["iou"], 0.5)
        self.assertEqual(self.created[-1]["rn_solution"], "C")

    def test_one_pixel_below_half_is_partial(self):
        self.login()
        self.approve_px(0, 0, 5, 5)                                  # 기준 25픽셀
        body = self.post(px_box(0, 0, 3, 4)).json()                  # 사용자 12픽셀: 12/25 = 0.48 (절반보다 작다)
        self.assertEqual(body["overlap_px"], {"grid_size": 512, "intersection": 12, "union": 25})
        self.assertEqual(body["result"], "부분정답")
        self.assertEqual(self.created[-1]["rn_solution"], "H")

    def test_displayed_iou_may_round_to_half_but_verdict_follows_the_integers(self):
        """기준 511×511(261121칸), 사용자 256×510(130560칸): 2·inter = 261120 < union 이라 절반에 **한 칸** 모자란다.
        표시용 iou 는 0.49999… → 0.5 로 반올림되지만 판정은 부분정답이어야 한다(반올림값으로 판정하면 정답이 된다)."""
        self.login()
        self.approve_px(0, 0, 511, 511)
        below = self.post(px_box(0, 0, 256, 510)).json()
        self.assertEqual(below["overlap_px"], {"grid_size": 512, "intersection": 130560, "union": 261121})
        self.assertEqual(below["iou"], 0.5)                 # 표시값은 절반으로 보인다
        self.assertEqual(below["result"], "부분정답")        # 그래도 판정은 정수 비교
        self.assertEqual(self.created[-1]["rn_solution"], "H")
        above = self.post(px_box(0, 0, 256, 511)).json()    # 130816칸: 2·inter = 261632 ≥ 261121
        self.assertEqual(above["overlap_px"]["intersection"], 130816)
        self.assertEqual(above["result"], "정답")

    def test_exactly_one_fifth_is_partial_and_below_is_wrong(self):
        self.login()
        self.approve_px(0, 0, 5, 5)                                  # 기준 25픽셀
        partial = self.post(px_box(0, 0, 5, 1)).json()               # 5/25 = 0.2 정확히
        self.assertEqual(partial["overlap_px"]["intersection"], 5)
        self.assertEqual(partial["result"], "부분정답")
        self.approve_px(0, 0, 5, 6)                                  # 기준 30픽셀
        wrong = self.post(px_box(0, 0, 5, 1)).json()                 # 5/30 < 0.2
        self.assertEqual(wrong["result"], "오답")

    def test_overlap_counts_explain_the_verdict(self):
        self.ready()
        body = self.post({"x": "0.1", "y": "0.3", "width": "0.4", "height": "0.4"}).json()
        counts = body["overlap_px"]
        self.assertEqual(set(counts), {"grid_size", "intersection", "union"})
        self.assertTrue(all(isinstance(v, int) and not isinstance(v, bool) for v in counts.values()))
        self.assertEqual(counts["grid_size"], 512)
        self.assertEqual(grade.verdict_from_px(counts["intersection"], counts["union"]), body["result"])
        self.assertEqual(body["iou"], round(counts["intersection"] / counts["union"], 5))

    def test_overlap_is_none_when_not_graded(self):
        self.login()
        self.assertIsNone(self.post().json()["overlap_px"])

    def test_reference_that_rasterizes_to_nothing_is_not_graded(self):
        """폭이 너무 좁아 격자에서 0픽셀이 되는 기준은 승인으로 보지 않는다(모든 답이 오답이 되는 것을 막는다)."""
        self.ready(x=0.3, width=0.0004)
        body = self.post().json()
        self.assertFalse(body["graded"])
        self.assertEqual(body["not_graded_reason"], "reference_not_approved")
        self.assertEqual(self.created, [])
        self.assertEqual(self.published, [])


class BoxOnlyTests(Base):
    """사용자 결정(2026-10-06): 도형은 박스만. 원은 거절하고, 박스는 이미지 안에 있어야 한다."""

    def test_circle_is_rejected_and_nothing_is_saved(self):
        self.ready()
        res = self.post({"roi_type": "circle", "cx": "0.5", "cy": "0.5", "radius": "0.2", "x": "", "y": "", "width": "", "height": ""})
        self.assertEqual(res.status_code, 400, res.text)
        self.assertIn("박스", res.json()["detail"])
        self.assertEqual(self.created, [])
        self.assertEqual(self.published, [])

    def test_only_box_is_accepted_as_shape_name(self):
        self.ready()
        for name in ("circle", "ellipse", "원", "rect", "rectangle", "triangle"):
            with self.subTest(roi_type=name):
                res = self.post({"roi_type": name})
                self.assertEqual(res.status_code, 400, res.text)
        # 빈 값은 FastAPI 가 "필수 항목 누락"(422)으로 먼저 거절한다. 어느 쪽이든 채점·저장하지 않는다.
        self.assertIn(self.post({"roi_type": ""}).status_code, (400, 422))
        self.assertEqual(self.created, [])
        self.assertEqual(self.post({"roi_type": " Box "}).status_code, 200)  # 대소문자·앞뒤 공백만 허용

    def test_circle_parameters_are_not_part_of_the_endpoint(self):
        import inspect

        params = set(inspect.signature(kneeLearning.learning_roi_grade_knee).parameters)
        self.assertFalse(params & {"cx", "cy", "radius"})
        self.ready()
        with_extras = self.post({"cx": "0.5", "cy": "0.5", "radius": "0.2"}).json()   # 보내도 쓰지 않는다
        plain = self.post().json()
        self.assertEqual(with_extras["iou"], plain["iou"])

    def test_box_must_be_inside_the_image(self):
        self.ready()
        bad = ({"x": "-0.1"}, {"y": "-0.1"}, {"x": "0.8", "width": "0.4"}, {"y": "0.8", "height": "0.4"},
               {"width": "-0.4"}, {"height": "-0.4"}, {"width": "0"}, {"height": "0"}, {"x": "1.5"}, {"width": "2"})
        for data in bad:
            with self.subTest(data=data):
                res = self.post(data)
                self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self.created, [])
        self.assertEqual(self.published, [])

    def test_box_touching_the_image_edges_is_accepted(self):
        self.ready()
        self.assertEqual(self.post({"x": "0", "y": "0", "width": "1", "height": "1"}).status_code, 200)
        self.assertEqual(self.post({"x": "0.6", "y": "0.6", "width": "0.4", "height": "0.4"}).status_code, 200)


class SlideStudyTests(Base):
    """슬라이드(여러 장) 문제: 서버가 정한 대표 슬라이스 1장만 채점한다(대표 슬라이스 설계). st_image 가 JSON 배열 문자열이면 슬라이드다.

    이전(9차)에는 슬라이드를 일괄 거절(slide_not_supported)했다. 이제는 대표 슬라이스 흐름으로 보내며, 승인표가 비어 있어
    승인된 대표가 없으면 no_reference_for_slice 로 채점하지 않는다. 자세한 시험은 tests/test_knee_slice.py.
    """
    SLIDE = '["/img/knee3-1.png", "/img/knee3-2.png"]'

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"KNEE_ASSET_ROOTS": "/img/"})   # 슬라이드 경로를 허용할 자산 루트
        env.start()
        self.addCleanup(env.stop)

    def use_image(self, value):
        self.study = SimpleNamespace(**{**STUDY.__dict__, "st_image": value})

    def test_slide_study_without_an_approved_representative_is_not_graded(self):
        self.ready()
        self.use_image(self.SLIDE)
        res = self.post({"slice_index": "0"})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertFalse(body["graded"])
        self.assertEqual(body["not_graded_reason"], "no_reference_for_slice")
        self.assertIn("대표 영상", body["not_graded_message"])
        for key in ("result", "iou", "overlap_px", "reference", "reference_bbox", "plane", "review_idx"):
            self.assertIsNone(body.get(key), key)
        self.assertEqual(self.created, [])
        self.assertEqual(self.published, [])
        self.assertEqual(body["next"]["idx"], 99)                      # 다음 문제는 계속 안내한다
        self.assertNotIn("knee3-1", res.text)                          # 이미지 경로를 응답에 싣지 않는다

    def test_unusable_image_values_are_not_graded(self):
        self.ready()
        # 배열처럼 보이지만 목록으로 쓸 수 없는 값은 slice_index 가 있어도 study_image_unavailable
        slides = {"[": "study_image_unavailable", "[]": "study_image_unavailable", '  ["/a.png"]': "study_image_unavailable",
                  '["/other/a.png"]': "study_image_unavailable"}                # 마지막은 허용 루트(/img/) 밖
        singles = {"": "study_image_unavailable", "   ": "study_image_unavailable", None: "study_image_unavailable",
                   '{"a": 1}': "study_image_unavailable", '"/img/a.png"': "study_image_unavailable"}
        for value, reason in {**slides, **singles}.items():
            with self.subTest(st_image=value):
                self.use_image(value)
                body = self.post({"slice_index": "0"}).json()
                self.assertFalse(body["graded"])
                self.assertEqual(body["not_graded_reason"], reason)
        self.assertEqual(self.created, [])
        self.assertEqual(self.published, [])

    def test_slide_study_requires_slice_index(self):
        self.ready()
        self.use_image(self.SLIDE)
        for data in ({}, {"slice_index": ""}, {"slice_index": "x"}):
            with self.subTest(data=data):
                self.assertEqual(self.post(data).status_code, 400)
        self.assertEqual(self.created, [])

    def test_bad_roi_is_still_rejected_on_a_slide_study(self):
        self.ready()
        self.use_image(self.SLIDE)
        self.assertEqual(self.post({"roi_type": "circle", "slice_index": "0"}).status_code, 400)
        self.assertEqual(self.post({"width": "0", "slice_index": "0"}).status_code, 400)

    def test_single_image_path_is_saved_as_is(self):
        self.ready()
        self.use_image("  /img/knee3.png ")
        body = self.post().json()
        self.assertTrue(body["graded"])
        self.assertEqual(self.created[0]["rn_image"], "/img/knee3.png")   # 앞뒤 공백을 뺀 단일 경로만 저장
        self.assertNotIn("[", self.created[0]["rn_image"])


class ResponseContractTests(Base):
    def test_no_abnormality_or_brain_classification_fields(self):
        self.ready()
        graded = self.post().json()
        for banned in ("has_abnormality", "abnormality_bbox", "classification", "germinoma", "overlay_png_base64",
                       "recall", "precision", "near_center", "center_distance_px"):
            self.assertNotIn(banned, graded)
        self.assertEqual(graded["reference"]["kind"], "case_reference_roi")
        self.assertEqual(graded["reference_bbox"], {"x": 0.3, "y": 0.3, "width": 0.4, "height": 0.4})
        self.assertEqual(set(graded["user_roi_bbox"]), {"x", "y", "width", "height"})
        self.assertNotIn("reviewed_by", graded["reference"])  # 검수자 이름은 응답에 넣지 않는다

    def test_not_graded_body_has_review_idx_null_and_graded_body_has_the_number(self):
        """015-02: 미채점 응답에도 review_idx 키가 있고 null, 저장되면 실제 번호."""
        self.login()
        body = self.post().json()                                   # 승인된 기준 없음
        self.assertIn("review_idx", body)
        self.assertIsNone(body["review_idx"])
        self.use_image_value(None)
        body = self.post().json()                                   # 문제 이미지 확인 불가
        self.assertEqual(body["not_graded_reason"], "study_image_unavailable")
        self.assertIn("review_idx", body)
        self.assertIsNone(body["review_idx"])
        self.study = STUDY
        self.approve()
        graded = self.post().json()
        self.assertTrue(graded["graded"])
        self.assertEqual(graded["review_idx"], 11)

    def use_image_value(self, value):
        self.study = SimpleNamespace(**{**STUDY.__dict__, "st_image": value})

    def test_not_graded_body_has_same_keys_without_answer(self):
        self.login()
        body = self.post().json()
        self.assertIsNone(body["reference"])
        self.assertIsNone(body["reference_bbox"])
        self.assertTrue(body["notices"])
        for banned in ("has_abnormality", "abnormality_bbox", "classification"):
            self.assertNotIn(banned, body)


class RequestValidationTests(Base):
    def test_bad_roi(self):
        self.ready()
        for data in ({"roi_type": "triangle"}, {"width": ""}, {"width": "0", "height": "0"},
                     {"roi_type": "circle", "cx": "0.5", "cy": "0.5", "radius": "0", "x": "", "y": "", "width": "", "height": ""},
                     {"x": "nan"}, {"width": "inf"}, {"x": ""}, {"height": ""},
                     {"roi_type": "circle", "cx": "0.5", "cy": "0.5", "radius": "inf", "x": "", "y": "", "width": "", "height": ""}):
            with self.subTest(data=data):
                res = self.post(data)
                self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self.created, [])

    def test_roi_is_checked_even_when_reference_missing(self):
        self.login()
        self.assertEqual(self.post({"roi_type": "triangle"}).status_code, 400)


class FailureTests(Base):
    def test_save_failure_is_safe_502_and_no_event(self):
        self.ready()

        def failing(db, **kw):
            raise RuntimeError("password=hunter2 host=10.0.0.5")

        with mock.patch.object(kneeLearning.review_node_repositories, "create", failing):
            res = self.post()
        self.assertEqual(res.status_code, 502)
        self.assertNotIn("hunter2", res.text)
        self.assertNotIn("10.0.0.5", res.text)
        self.assertEqual(self.published, [])

    def test_no_next_problem(self):
        self.ready()
        self.next_row = None
        self.assertIsNone(self.post().json()["next"])

    def test_next_problem_lookup_error_is_ignored(self):
        self.ready()

        def bad(db, **kw):
            raise ValueError("no problem")

        with mock.patch.object(kneeLearning.learning_service, "get_random_problem", bad):
            self.assertIsNone(self.post().json()["next"])


class ModuleTests(unittest.TestCase):
    def test_modules_do_not_import_torch(self):
        import subprocess

        root = Path(__file__).resolve().parents[1]
        code = ("import sys; sys.path.insert(0, r'%s'); import services.kneeRoiGradeServices, services.kneeSliceGradeServices; "
                "import importlib; "
                "[importlib.import_module('class.knee.' + n) for n in ('reference_rois', 'slice_manifest', 'slide_references', 'problem_token')]; "
                "print('torch' in sys.modules)" % root)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)

    def test_reference_tables_are_empty_by_default(self):
        self.assertEqual(refs.APPROVED_REFERENCES, {})
        # 대표 장 승인표는 사람이 채우는 데이터 파일(slide_references_data.py)의 행과 정확히 같다(비어 있으면 빈 표)
        data = import_module("class.knee.slide_references_data").REFERENCE_ROWS
        table = import_module("class.knee.slide_references").APPROVED_SLIDE_REFERENCES
        self.assertEqual(sorted((r.study_idx, r.reference_id) for rows in table.values() for r in rows), sorted((r["study_idx"], r["reference_id"]) for r in data))


if __name__ == "__main__":
    unittest.main()
