"""
무릎 학습 화면(GET /learn/knee)과 문제 제시(GET /learning/knee/task), 기존 채점 API 연결 시험.

- 화면 정적 규칙(외부 주소·저장소·원 도형·학습 목적 문구)과 라우트는 DB 없이 확인한다.
- 문제 제시·채점·저장은 tests/knee_screen_harness.py 의 임시 PostgreSQL 에서 **실제 코드**로 확인한다
  (시험 전용 로그인·합성 영상·임시 승인표는 이 시험 환경에만 있다. 운영 코드에는 없다).
- 브라우저 동작(박스 그리기 등)은 tests/js(node)와 헤드리스 브라우저 시험으로 따로 확인한다.
실행: python -m pytest tests/test_knee_screen.py   (pgserver, psycopg, pillow 필요. 없으면 DB 시험은 건너뜀)
"""
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from routers import kneeLearning

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "knee_learning.html"
JS_FILES = [ROOT / "assets" / "js" / "knee-learning.js", ROOT / "assets" / "js" / "knee-learning-core.js"]
refs = import_module("class.knee.slide_references")
tokens = import_module("class.knee.problem_token")

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


# ── 화면·정적 규칙 (DB 없음) ────────────────────────────────────────────────────
class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(kneeLearning.router)
        app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
        cls.client = TestClient(app)

    def test_pages_open_and_modes_differ(self):
        demo = self.client.get("/learn/knee", params={"demo": "1"})
        live = self.client.get("/learn/knee", params={"study_idx": "3"})
        self.assertEqual((demo.status_code, live.status_code), (200, 200))
        self.assertIn('data-mode="demo"', demo.text)
        self.assertIn("실제 서버 채점이 아닙니다", demo.text)
        self.assertIn('id="scenario"', demo.text)
        self.assertIn('data-mode="live"', live.text)
        self.assertIn('data-study="3"', live.text)
        self.assertNotIn("실제 서버 채점이 아닙니다", live.text)
        self.assertNotIn('id="scenario"', live.text)   # 시험용 응답 선택은 시연에서만

    def test_study_idx_is_validated_before_it_reaches_the_page(self):
        for bad in ("abc", "1.5", "1e3", "٣"):
            res = self.client.get("/learn/knee", params={"study_idx": bad})
            self.assertEqual(res.status_code, 422, bad)
        for non_positive in ("0", "-1"):   # 양의 정수가 아니면 화면에 번호를 싣지 않는다(화면이 "문제 번호가 필요합니다"를 안내)
            res = self.client.get("/learn/knee", params={"study_idx": non_positive})
            self.assertEqual(res.status_code, 200)
            self.assertIn('data-study=""', res.text, non_positive)

    def test_purpose_and_basis_text_are_always_shown(self):
        for params in ({"demo": "1"}, {"study_idx": "3"}):
            text = self.client.get("/learn/knee", params=params).text
            self.assertIn("ACL이 포함된 참고 영역 표시 연습", text)
            self.assertIn("데이터셋 라벨 기준, 임상 검증 아님", text)
            self.assertIn("파열 병변이나 인대의 정확한 경계를 뜻하지 않습니다", text)
            self.assertIn("대표 영상 한 장", text)

    def test_no_circle_tool_no_external_resources_no_inline_script(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertNotRegex(text, r'value="circle"|radius|<circle')
        self.assertIsNone(re.search(r"(?:src|href)=[\"']https?://", text))
        self.assertIsNone(re.search(r"@import|https?://", (ROOT / "assets/css/knee-learning.css").read_text(encoding="utf-8")))
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", text, re.S)
        self.assertEqual(len(scripts), 2)
        for attrs, body in scripts:
            self.assertIn("src=", attrs)
            self.assertEqual(body.strip(), "")   # 화면 안에 직접 쓴 스크립트 없음

    def test_scripts_do_not_store_or_grade(self):
        for path in JS_FILES:
            code = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)   # 설명 주석은 제외하고 실제 코드만 본다
            code = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", code)
            for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie", "eval(", "document.write", "innerHTML", "console.log"):
                self.assertFalse(forbidden in code, f"{path.name}: {forbidden}")
            for grading_word in ("intersection", "union", "overlap_px", "reference_rois", "APPROVED"):
                self.assertFalse(grading_word in code, f"{path.name}: {grading_word}")
            self.assertIsNone(re.search(r"\bapproved\b", code), f"{path.name}: approved")   # 사유 이름(reference_not_approved)은 허용

    def test_recovery_and_change_notice_elements_exist_outside_the_result_card(self):
        """FINDING-SCREEN-017-01/02: 다시 시도 버튼은 결과 카드 밖(영상 자리)에 있고, 영상 변경 안내와 선택지가 있다."""
        text = TEMPLATE.read_text(encoding="utf-8")
        card = text[text.index('id="resultCard"'):text.index("</section>", text.index('id="resultCard"'))]
        for element_id in ("btnStageRetry", "stageRetry", "changeNotice", "btnUseOldBox", "btnRedraw", "basisReview"):
            self.assertIn(f'id="{element_id}"', text, element_id)
            if element_id != "btnReopen":
                self.assertNotIn(f'id="{element_id}"', card, f"{element_id} 가 결과 카드 안에 있으면 카드가 숨겨질 때 같이 사라진다")

    def test_review_completed_sentence_is_not_static(self):
        """NOTE: 검수 완료 문장은 승인된 문제가 열렸을 때만 화면 스크립트가 보여 준다. 정적 HTML 에는 없다."""
        done = "팀원이 데이터셋의 위치 라벨을 확인했습니다"
        self.assertNotIn(done, TEMPLATE.read_text(encoding="utf-8"))
        for params in ({"demo": "1"}, {"study_idx": "3"}):
            self.assertNotIn(done, self.client.get("/learn/knee", params=params).text, params)
        self.assertIn("합성 이미지이며 검수된 기준이 아닙니다", self.client.get("/learn/knee", params={"demo": "1"}).text)
        self.assertIn("라벨 확인을 마친 문제에만 제공됩니다", self.client.get("/learn/knee", params={"study_idx": "3"}).text)
        js = (ROOT / "assets/js/knee-learning.js").read_text(encoding="utf-8")
        self.assertIn(done, js)
        self.assertRegex(js, r'setBasis\(MODE === "demo" \? "demo" : "reviewed"\)')   # 승인된 live 문제가 열린 뒤에만 완료 문구

    def test_same_origin_wording_not_same_site(self):
        """NOTE: 쿠키 세션은 상대 주소 + same-origin fetch 이므로 '같은 사이트'가 아니라 '같은 Origin' 이라고 적는다."""
        for path in (ROOT / "assets/js/knee-learning.js", ROOT / "assets/js/knee-learning-core.js", ROOT / "docs/knee.html"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("같은 사이트", text, path.name)
        self.assertIn("같은 Origin", (ROOT / "assets/js/knee-learning.js").read_text(encoding="utf-8"))

    def test_response_loss_limit_is_documented_in_the_screen_code(self):
        js = (ROOT / "assets/js/knee-learning.js").read_text(encoding="utf-8")
        self.assertIn("한 번 더 남을 수 있다", js)
        self.assertIn("\"저장되지 않았다\"고 단정하지 않는다", js)

    def test_login_form_is_live_only_and_never_puts_values_in_the_url(self):
        """② 로그인 칸: 실제 연결 화면에만 있고, 입력칸에 name 속성이 없어 스크립트가 없어도 값이 주소로 가지 않는다."""
        live = self.client.get("/learn/knee", params={"study_idx": "3"}).text
        demo = self.client.get("/learn/knee", params={"demo": "1"}).text
        self.assertIn('id="loginForm"', live)
        self.assertNotIn('id="loginForm"', demo)
        form = live[live.index('id="loginForm"'):live.index("</form>", live.index('id="loginForm"'))]
        self.assertRegex(form, r'<input id="loginPw" type="password" autocomplete="current-password"')
        self.assertRegex(form, r'<input id="loginId" type="text" autocomplete="username"')
        self.assertIsNone(re.search(r"<input[^>]*\sname=", form), "입력칸에 name 속성이 있으면 폼이 값을 주소로 보낼 수 있다")
        self.assertIsNone(re.search(r"<form[^>]*\s(action|method)=", form))
        js = (ROOT / "assets/js/knee-learning.js").read_text(encoding="utf-8")
        self.assertIn("res.status", js)
        self.assertNotIn("res.json", js[js.index("async function submitLogin"):js.index("if (el.loginForm) el.loginForm.addEventListener")])   # 로그인 응답 본문은 읽지 않는다

    def test_static_files_are_served(self):
        for url in ("/assets/js/knee-learning.js", "/assets/js/knee-learning-core.js", "/assets/css/knee-learning.css"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_task_api_is_closed_by_default(self):
        res = self.client.get("/learning/knee/task", params={"study_idx": 3})
        self.assertIn(res.status_code, (401, 503))   # 기본은 꺼짐(.env 없이도 안전한 쪽)

    def test_core_rules_in_node(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 가 없어 화면 핵심 규칙 시험을 건너뜀")
        done = subprocess.run([node, "--test", str(ROOT / "tests" / "js" / "knee_learning_core.test.js")],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:] + done.stderr[-1000:])


# ── 문제 제시·채점·저장 (임시 PostgreSQL, 실제 코드) ────────────────────────────────
@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class ConnectedBase(unittest.TestCase):
    harness = None

    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.harness = Harness()

    @classmethod
    def tearDownClass(cls):
        if cls.harness is not None:
            cls.harness.close()

    def setUp(self):
        self.h = self.harness
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        self.addCleanup(setattr, self.h.clock, "offset", 0)
        self.h.clock.offset = 0

    def login(self, idx=None, verified=True):
        res = self.client.post("/_login", params={"idx": idx or self.h.user_idx, "verified": str(verified).lower()})
        self.assertEqual(res.status_code, 200)

    def task(self, study=None):
        res = self.client.get("/learning/knee/task", params={"study_idx": study or self.h.study_idx})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["knee_task"]

    def grade(self, box=(0.3, 0.3, 0.4, 0.4), task=None, **over):
        form = {"study_idx": str(self.h.study_idx), "roi_type": "box",
                "x": f"{box[0]:.6f}", "y": f"{box[1]:.6f}", "width": f"{box[2]:.6f}", "height": f"{box[3]:.6f}"}
        if task is not None:
            form["slice_index"] = str(task["representative_slice_index"])
            form["problem_token"] = task["problem_token"]
        form.update({k: v for k, v in over.items() if v is not None})
        for key in [k for k, v in over.items() if v is None]:
            form.pop(key, None)
        return self.client.post("/learning/roi-grade/knee", data=form)

    def rows(self):
        return self.h.reviews()


@unittest.skipUnless(HAVE_DB_TOOLS, "임시 DB 도구 없음")
class TaskApiTests(ConnectedBase):
    def test_login_flow_and_closed_doors(self):
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, 401)                               # 비로그인
        self.login(verified=False)
        self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).status_code, 403)
        self.login(idx=self.h.gone_user_idx)                                  # 삭제된 회원은 세션이 있어도 막힌다
        self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).status_code, 401)

    def test_disabled_flag_closes_the_task_api(self):
        self.login()
        with mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": ""}):
            res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, 503)

    def test_not_a_knee_study_or_missing_study(self):
        self.login()
        for idx in (self.h.brain_study_idx, 99999):
            self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": idx}).status_code, 404, idx)
        self.assertEqual(self.client.get("/learning/knee/task").status_code, 422)   # study_idx 누락

    def test_approved_task_has_binding_fields_and_no_answer(self):
        self.login()
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.headers["cache-control"], "no-store")
        body = res.json()
        task = body["knee_task"]
        self.assertEqual(body["study_idx"], self.h.study_idx)
        self.assertTrue(task["grading_available"])
        self.assertEqual(task["representative_slice_index"], self.h.rep)
        self.assertEqual(task["display_slice_number"], self.h.rep + 1)
        self.assertEqual(task["slice_count"], 24)
        self.assertEqual((task["image_width"], task["image_height"]), (320, 384))
        self.assertTrue(task["image_url"].startswith("/_images/"))
        self.assertNotIn(self.h.paths[self.h.rep], json.dumps(body))           # 내부 경로 비노출
        self.assertEqual(task["coord_system"], "normalized_image_top_left")
        # 정답 박스·진단·출처·검수자 비노출
        dumped = json.dumps(body, ensure_ascii=False)
        for secret in ("reviewed_by", "reviewer-000", "source", "synthetic dataset", "st_disease", "ACL 파열", "reference_bbox", "0.3,"):
            self.assertNotIn(secret, dumped, secret)
        self.assertEqual(set(body), {"study_idx", "knee_task"})

    def test_unapproved_study_gives_reason_only(self):
        self.login()
        task = self.task(self.h.unapproved_study_idx)
        self.assertFalse(task["grading_available"])
        self.assertEqual(task["unavailable_reason"], "no_reference_for_slice")
        self.assertIsNone(task["problem_token"])
        self.assertIsNone(task["image_url"])

    def test_asset_provider_failures_are_closed_and_do_not_leak(self):
        self.login()
        url = "/learning/knee/task"
        params = {"study_idx": self.h.study_idx}
        kneeLearning.set_asset_provider(None)
        try:
            self.assertEqual(self.client.get(url, params=params).status_code, 503)

            def boom(ref, manifest):
                raise RuntimeError("secret NAS host nas.internal:5000")
            kneeLearning.set_asset_provider(boom)
            res = self.client.get(url, params=params)
            self.assertEqual(res.status_code, 503)
            self.assertNotIn("nas.internal", res.text)
            kneeLearning.set_asset_provider(lambda ref, manifest: {"image_url": ref.image_path, "image_width": 320, "image_height": 384})
            self.assertEqual(self.client.get(url, params=params).status_code, 503)   # 내부 경로를 그대로 쓰는 공급자는 거절
            kneeLearning.set_asset_provider(lambda ref, manifest: {"image_url": "/x.png", "image_width": 0, "image_height": 384})
            self.assertEqual(self.client.get(url, params=params).status_code, 503)   # 크기가 올바르지 않음
        finally:
            kneeLearning.set_asset_provider(lambda ref, manifest: {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384})

    def test_no_signing_key_closes_the_task_api(self):
        self.login()
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": ""}):
            self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).status_code, 503)


@unittest.skipUnless(HAVE_DB_TOOLS, "임시 DB 도구 없음")
class TaskSnapshotTests(ConnectedBase):
    """FINDING-SCREEN-017-03: 영상 공급과 토큰 발급은 같은 승인·영상 버전(snapshot)에 묶여야 한다.

    공급자가 실행되는 동안 승인 기준이 바뀌거나 철회되거나 문제의 목록이 바뀌어도, 서로 다른 영상 주소와 토큰이 섞인
    "사용 가능한" 응답이 나가면 안 된다. 바뀌었으면 고정 오류(409)로 닫고 새 기준으로 자동 전환하지 않는다.
    """

    def setUp(self):
        super().setUp()
        self.login()
        self.addCleanup(self._restore)

    def _restore(self):
        kneeLearning.set_asset_provider(lambda ref, manifest: {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384})
        self.h.swap_reference(12, reference_id=7, version="v1")
        with self.h.Session() as db:
            from models.studyModel import StudyModel
            row = db.get(StudyModel, self.h.study_idx)
            row.st_image, row.del_yn, row.st_part, row.st_modal = json.dumps(self.h.paths), "N", "4", "3"
            db.commit()

    def get_task(self):
        return self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})

    def provider_that(self, hook):
        def provider(ref, manifest):
            hook()
            return {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384}
        kneeLearning.set_asset_provider(provider)

    def assert_closed(self, res):
        """사용 가능한 제시가 나가지 않았다(토큰·영상 주소 없음)."""
        self.assertNotEqual(res.status_code, 200, res.text)
        self.assertNotIn("problem_token", res.text)
        self.assertNotIn("image_url", res.text)

    def test_reference_swapped_while_providing_is_never_mixed(self):
        self.provider_that(lambda: self.h.swap_reference(13, reference_id=8, version="v2"))
        res = self.get_task()
        self.assertEqual(res.status_code, 409, res.text)
        self.assert_closed(res)

    def test_approval_withdrawn_while_providing_is_closed(self):
        self.provider_that(lambda: self.h.swap_reference(0, remove=True))
        res = self.get_task()
        self.assertEqual(res.status_code, 409, res.text)
        self.assert_closed(res)

    def test_slide_list_changed_while_providing_is_closed(self):
        def change_list():
            with self.h.Session() as db:
                from models.studyModel import StudyModel
                db.get(StudyModel, self.h.study_idx).st_image = json.dumps(self.h.paths[:-1])
                db.commit()
        self.provider_that(change_list)
        res = self.get_task()
        self.assertEqual(res.status_code, 409, res.text)
        self.assert_closed(res)

    def test_conflict_message_is_fixed_and_after_it_a_retry_gets_the_new_consistent_task(self):
        self.provider_that(lambda: self.h.swap_reference(13, reference_id=8, version="v2"))
        conflict = self.get_task()
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(set(conflict.json()), {"detail"})
        self.assertNotIn("v2", conflict.text)
        kneeLearning.set_asset_provider(lambda ref, manifest: {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384})
        retry = self.get_task()
        self.assertEqual(retry.status_code, 200, retry.text)
        task = retry.json()["knee_task"]
        self.assertEqual(task["representative_slice_index"], 13)
        self.assertEqual(task["image_url"], "/_images/rep-8.png")          # 새 기준의 영상 주소 + 새 기준의 대표 번호
        grade = self.grade(task=task)                                      # 그 토큰은 새 기준으로만 채점된다
        self.assertTrue(grade.json()["graded"])
        self.assertEqual(self.rows()[-1].rn_image, self.h.paths[13])

    def test_study_removed_or_retargeted_while_providing_is_closed(self):
        from models.studyModel import StudyModel
        for field, value in (("del_yn", "Y"), ("st_part", "1"), ("st_modal", "2")):
            def change(field=field, value=value):
                with self.h.Session() as db:
                    setattr(db.get(StudyModel, self.h.study_idx), field, value)
                    db.commit()
            self.provider_that(change)
            res = self.get_task()
            self.assertEqual(res.status_code, 409, f"{field}: {res.text}")
            self.assert_closed(res)
            self._restore()

    def test_stable_reference_is_unaffected(self):
        calls = []
        self.provider_that(lambda: calls.append(1))
        res = self.get_task()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(calls), 1)

    def test_presentation_id_is_stable_opaque_and_changes_with_the_presentation(self):
        sha12 = self.h.rep_sha
        first = self.get_task().json()["knee_task"]
        again = self.get_task().json()["knee_task"]
        self.assertTrue(re.fullmatch(r"[0-9a-f]{16}", first["presentation_id"]), first.get("presentation_id"))
        self.assertEqual(first["presentation_id"], again["presentation_id"])        # 토큰은 매번 달라도 같은 제시는 같은 값
        self.assertNotEqual(first["problem_token"], again["problem_token"])
        self.login(idx=self.h.other_user_idx)
        self.assertEqual(self.get_task().json()["knee_task"]["presentation_id"], first["presentation_id"])   # 사용자와 무관
        self.h.swap_reference(13, reference_id=8, version="v2")
        changed = self.get_task().json()["knee_task"]
        self.assertNotEqual(changed["presentation_id"], first["presentation_id"])   # 대표·영상·기준이 바뀌면 달라진다
        self.h.swap_reference(12, reference_id=7, version="v2")                     # 같은 장·같은 영상에서 기준 버전만 바뀌면 같은 영상이다
        self.assertEqual(self.get_task().json()["knee_task"]["presentation_id"], first["presentation_id"])
        dumped = json.dumps([first, changed])
        import hashlib
        sha13 = hashlib.sha256(self.h.png_by_path[self.h.paths[13]]).hexdigest()
        for secret in (sha12, sha13, self.h.paths[12], self.h.paths[13], "reviewer-000", "synthetic dataset"):
            self.assertNotIn(secret, dumped)                                         # 불투명: 경로·해시·버전을 드러내지 않는다

    def test_presentation_id_follows_the_image_content_and_slice_not_the_reference_version(self):
        from services import kneeSliceGradeServices as service
        key = b"k" * 40
        manifest, ref = self.h.manifest, self.h.ref
        base = service.presentation_id(key, study_idx=3, manifest=manifest, reference=ref)
        import dataclasses
        same_image_new_version = dataclasses.replace(ref, version="v9", reference_id=99)
        self.assertEqual(service.presentation_id(key, study_idx=3, manifest=manifest, reference=same_image_new_version), base)
        other_content = dataclasses.replace(ref, image_sha256="0" * 64)
        self.assertNotEqual(service.presentation_id(key, study_idx=3, manifest=manifest, reference=other_content), base)
        other_slice = dataclasses.replace(ref, slice_no=ref.slice_no + 1)
        self.assertNotEqual(service.presentation_id(key, study_idx=3, manifest=manifest, reference=other_slice), base)
        self.assertNotEqual(service.presentation_id(key, study_idx=4, manifest=manifest, reference=ref), base)
        self.assertNotEqual(service.presentation_id(b"z" * 40, study_idx=3, manifest=manifest, reference=ref), base)   # 키가 달라지면 값도 달라진다

    def test_unavailable_task_has_no_presentation_id(self):
        task = self.client.get("/learning/knee/task", params={"study_idx": self.h.unapproved_study_idx}).json()["knee_task"]
        self.assertFalse(task["grading_available"])
        self.assertIsNone(task["presentation_id"])


@unittest.skipUnless(HAVE_DB_TOOLS, "임시 DB 도구 없음")
class GradeConnectionTests(ConnectedBase):
    def test_graded_flow_saves_one_row_for_the_representative_path(self):
        self.login()
        task = self.task()
        before = len(self.rows())
        res = self.grade(task=task)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["graded"])
        self.assertEqual(body["result"], "정답")
        self.assertEqual(body["iou"], 1.0)
        self.assertEqual(body["slice_index"], self.h.rep)
        self.assertFalse(body["clinical_validated"])
        self.assertEqual(body["reference_basis"], "dataset_acl_region")
        rows = self.rows()
        self.assertEqual(len(rows), before + 1)
        row = rows[-1]
        self.assertEqual((row.rn_u_idx, row.rn_s_idx), (self.h.user_idx, self.h.study_idx))
        self.assertEqual(row.rn_image, self.h.paths[self.h.rep])            # 배열 문자열이 아닌 단일 대표 경로
        self.assertEqual(row.rn_solution, "C")
        self.assertEqual(body["review_idx"], row.idx)
        self.assertNotIn("[", row.rn_image)

    def test_partial_and_different_results(self):
        self.login()
        task = self.task()
        partial = self.grade(box=(0.5, 0.3, 0.4, 0.4), task=task).json()      # IoU 약 0.33
        self.assertEqual((partial["graded"], partial["result"]), (True, "부분정답"))
        self.assertTrue(0.2 <= partial["iou"] < 0.5)
        differ = self.grade(box=(0.0, 0.0, 0.2, 0.2), task=task).json()        # 겹침 0
        self.assertEqual((differ["graded"], differ["result"], differ["iou"]), (True, "오답", 0.0))

    def test_screen_box_and_graded_position_agree(self):
        """화면이 보낸 6자리 박스 = 서버가 채점에 쓴 위치(정수 격자 오차 이내). 서버 IoU 도 독립 계산과 맞는다."""
        self.login()
        task = self.task()
        rnd = random.Random(7)
        ref = self.h.ref_box
        for _ in range(25):
            x, y = rnd.uniform(0, 0.8), rnd.uniform(0, 0.8)
            w, h = rnd.uniform(0.05, 1 - x), rnd.uniform(0.05, 1 - y)
            sent = [round(v, 6) for v in (x, y, w, h)]
            sent[2] = min(sent[2], round(1 - sent[0], 6))
            sent[3] = min(sent[3], round(1 - sent[1], 6))
            body = self.grade(box=tuple(sent), task=task).json()
            self.assertTrue(body["graded"], body)
            got = body["user_roi_bbox"]
            for key, expected in zip(("x", "y", "width", "height"), sent):
                self.assertLessEqual(abs(got[key] - expected), 1 / 512 + 1e-6, (key, got, sent))
            ix = max(0.0, min(sent[0] + sent[2], ref[0] + ref[2]) - max(sent[0], ref[0]))
            iy = max(0.0, min(sent[1] + sent[3], ref[1] + ref[3]) - max(sent[1], ref[1]))
            inter = ix * iy
            union = sent[2] * sent[3] + ref[2] * ref[3] - inter
            self.assertLessEqual(abs(body["iou"] - inter / union), 0.02, (sent, body["iou"], inter / union))

    def test_representative_slice_is_bound_to_the_presented_task(self):
        self.login()
        task = self.task()
        before = len(self.rows())
        # 범위 안의 다른 장: 채점·저장 없음, review_idx 없음
        other = self.grade(task=task, slice_index="5").json()
        self.assertEqual((other["graded"], other["not_graded_reason"], other["review_idx"]), (False, "no_reference_for_slice", None))
        self.assertIsNone(other["result"])
        self.assertIsNone(other["iou"])
        # 범위 밖·형식 오류·누락은 400(임의 첫 장으로 바꾸지 않는다)
        for bad in ("24", "999999", "-1", "1.5", "+3", " 3", "abc", "", "٣"):
            self.assertEqual(self.grade(task=task, slice_index=bad).status_code, 400, repr(bad))
        self.assertEqual(self.grade(task=task, slice_index=None).status_code, 400)
        self.assertEqual(len(self.rows()), before)

    def test_token_problems_are_not_graded(self):
        self.login()
        task = self.task()
        before = len(self.rows())
        tampered = task["problem_token"][:-3] + ("AAA" if not task["problem_token"].endswith("AAA") else "BBB")
        for token, reason in ((tampered, "token_invalid"), ("garbage", "token_invalid"), ("", "token_invalid")):
            res = self.grade(task=dict(task, problem_token=token)).json()
            self.assertEqual((res["graded"], res["not_graded_reason"], res["review_idx"]), (False, reason, None), token[:10])
        no_token = self.grade(task=task, problem_token=None).json()           # 토큰 누락
        self.assertEqual((no_token["graded"], no_token["not_graded_reason"]), (False, "token_invalid"))
        # 다른 학습자의 토큰
        self.login(idx=self.h.other_user_idx)
        stolen = self.grade(task=task).json()
        self.assertEqual((stolen["graded"], stolen["not_graded_reason"]), (False, "token_invalid"))
        self.assertEqual(len(self.rows()), before)

    def test_expired_token_then_reopen_works_and_reuse_before_expiry_is_allowed(self):
        self.login()
        task = self.task()
        first, second = self.grade(task=task).json(), self.grade(task=task).json()   # 만료 전 재사용 허용
        self.assertTrue(first["graded"] and second["graded"])
        before = len(self.rows())
        self.assertEqual(self.client.post("/_advance", params={"seconds": 7201}).status_code, 200)
        late = self.grade(task=task).json()
        self.assertEqual((late["graded"], late["not_graded_reason"], late["review_idx"]), (False, "token_expired", None))
        self.assertEqual(len(self.rows()), before)
        reopened = self.task()                                                      # 문제를 다시 열면 새 토큰
        self.assertNotEqual(reopened["problem_token"], task["problem_token"])
        self.assertTrue(self.grade(task=reopened).json()["graded"])

    def test_changed_reference_invalidates_old_presentation(self):
        self.login()
        task = self.task()
        old = self.h.ref
        newer = refs.SlideReference(**{**old.__dict__, "reference_id": 8, "version": "v2"})
        with mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, {self.h.study_idx: [newer]}):
            res = self.grade(task=task).json()
        self.assertEqual((res["graded"], res["not_graded_reason"]), (False, "presentation_changed"))

    def test_unapproved_and_ambiguous_studies_are_not_graded(self):
        self.login()
        before = len(self.rows())
        form = {"study_idx": str(self.h.unapproved_study_idx), "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4",
                "slice_index": "12"}
        res = self.client.post("/learning/roi-grade/knee", data=form).json()
        self.assertEqual((res["graded"], res["not_graded_reason"], res["review_idx"]), (False, "no_reference_for_slice", None))
        twin = refs.SlideReference(**{**self.h.ref.__dict__, "reference_id": 9})
        with mock.patch.dict(refs.APPROVED_SLIDE_REFERENCES, {self.h.study_idx: [self.h.ref, twin]}):
            amb = self.grade(task=self.task_for_ambiguous()).json()
        self.assertEqual((amb["graded"], amb["not_graded_reason"]), (False, "reference_ambiguous"))
        self.assertEqual(len(self.rows()), before)

    def task_for_ambiguous(self):
        return {"representative_slice_index": self.h.rep, "problem_token": "ignored"}

    def test_bad_boxes_are_rejected_without_saving(self):
        self.login()
        task = self.task()
        before = len(self.rows())
        cases = [
            dict(x="0.7", y="0.3", width="0.4", height="0.4"),     # 이미지 밖(오른쪽)
            dict(x="0.3", y="0.7", width="0.4", height="0.4"),     # 이미지 밖(아래)
            dict(x="-0.1", y="0.3", width="0.4", height="0.4"),
            dict(x="0.3", y="0.3", width="0", height="0.4"),
            dict(x="0.3", y="0.3", width="-0.4", height="0.4"),
            dict(x="nan", y="0.3", width="0.4", height="0.4"),
            dict(x="0.3", y="0.3", width="inf", height="0.4"),
            dict(roi_type="circle"),
            dict(roi_type="circle", x=None, y=None, width=None, height=None),
        ]
        for over in cases:
            res = self.grade(task=task, **over)
            self.assertEqual(res.status_code, 400, over)
        self.assertEqual(len(self.rows()), before)

    def test_access_rules_for_grading(self):
        task = None
        self.login()
        task = self.task()
        self.client.post("/_logout")
        self.assertEqual(self.grade(task=task).status_code, 401)               # 비로그인
        self.login(verified=False)
        self.assertEqual(self.grade(task=task).status_code, 403)
        self.login(idx=self.h.gone_user_idx)
        self.assertEqual(self.grade(task=task).status_code, 401)
        self.login()
        res = self.client.post("/learning/roi-grade/knee", data={"study_idx": str(self.h.brain_study_idx), "roi_type": "box",
                                                                 "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4"})
        self.assertEqual(res.status_code, 404)                                  # 무릎 문제가 아님
        with mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": ""}):
            self.assertEqual(self.grade(task=task).status_code, 503)

    def test_user_idx_comes_from_the_session_only(self):
        self.login(idx=self.h.other_user_idx)
        task_other = self.task()
        res = self.grade(task=task_other, user_idx=str(self.h.user_idx))       # 요청의 user_idx 는 무시된다
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.rows()[-1].rn_u_idx, self.h.other_user_idx)


if __name__ == "__main__":
    unittest.main()
