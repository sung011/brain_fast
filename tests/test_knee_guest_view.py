"""
무릎 MRI 게스트 실제 영상 열람(MSN-20261016-002) 시험. 설계: designs/MSN-20261016-002/DEMO_REAL_VIEW_PLAN.md, ACCEPTANCE_TESTS.md(AC01~07·AC10 서버 부분).

임시 PostgreSQL·합성 케이스 3개(24·18·31장)·임시 서버 폴더에서 **실제 라우터 코드**로 확인한다. 게스트 스위치·허용 Origin 은 이 시험 프로세스 환경에만 넣는다.
실행: python -m pytest tests/test_knee_guest_view.py   (pgserver, psycopg, pillow 필요)
"""
import hashlib
import json
import logging
import os
import sys
import threading
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

guest = import_module("services.kneeGuestViewServices")
series_assets = import_module("class.knee.series_assets")

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False

FRONT = "http://front.test:8091"
G = "/learning/knee/guest"


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class GuestViewTests(unittest.TestCase):
    harness = None

    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.harness = Harness(series=True, extra_cases=(18, 31), guest_origins=FRONT)

    @classmethod
    def tearDownClass(cls):
        if cls.harness is not None:
            cls.harness.close()

    def setUp(self):
        self.h = self.harness
        self.clock = FakeClock()
        self.store = guest.reset_store(clock=self.clock)
        self.client = self.new_client()
        self.ids = sorted([self.h.study_idx] + [c["study_idx"] for c in self.h.extra])
        self.counts = {self.h.study_idx: 24, self.h.extra[0]["study_idx"]: 18, self.h.extra[1]["study_idx"]: 31}

    def new_client(self):
        return TestClient(self.h.app, raise_server_exceptions=False)

    def start(self, client=None, origin=FRONT):
        client = client or self.client
        return client.post(f"{G}/session", headers={"Origin": origin} if origin else {})

    def random(self, client=None, exclude=None):
        client = client or self.client
        return client.get(f"{G}/random", params={} if exclude is None else {"exclude": exclude})

    def series(self, idx, client=None):
        return (client or self.client).get(f"{G}/series", params={"study_idx": idx})

    def started_case(self, client=None):
        client = client or self.client
        self.assertEqual(self.start(client).status_code, 200)
        r = self.random(client)
        self.assertEqual(r.status_code, 200, r.text)
        idx = r.json()["study_idx"]
        s = self.series(idx, client)
        self.assertEqual(s.status_code, 200, s.text)
        return idx, s.json()

    # ── AC01 기본 꺼짐·독립성 ──
    def test_ac01_off_closes_every_guest_route_before_db_or_files(self):
        with mock.patch.dict(os.environ, {"KNEE_GUEST_VIEW_ENABLED": "0"}), \
                mock.patch.object(import_module("routers.kneeLearning").study_repositories, "get_by_idx", side_effect=AssertionError("DB")), \
                mock.patch.object(import_module("routers.kneeLearning"), "_image_source", side_effect=AssertionError("source")):
            calls = [self.start(), self.client.post(f"{G}/session/end", headers={"Origin": FRONT}), self.random(), self.series(self.h.study_idx),
                     self.client.get(f"{G}/series/{self.h.study_idx}/abc/image/0")]
            for r in calls:
                self.assertEqual((r.status_code, r.json()["error"]), (503, "guest_view_disabled"))
                self.assertEqual(r.headers["cache-control"], "no-store")
        # 학습자 기능은 게스트 스위치와 무관하게 그대로(시험 로그인)
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx}).status_code, 200)
        with mock.patch.dict(os.environ, {"KNEE_GUEST_VIEW_ENABLED": "0"}):
            self.assertEqual(self.client.get("/learning/knee/series", params={"study_idx": self.h.study_idx}).status_code, 200)

    def test_ac01_missing_origin_config_closes_guest(self):
        with mock.patch.dict(os.environ, {"KNEE_GUEST_ALLOWED_ORIGINS": ""}):
            self.assertEqual(self.start().json()["error"], "guest_view_not_configured")
        with mock.patch.dict(os.environ, {"KNEE_GUEST_ALLOWED_ORIGINS": "*"}):
            self.assertEqual(self.start().status_code, 503)

    # ── AC02 세션 분리 ──
    def test_ac02_session_uses_separate_httponly_cookie_and_no_learner_session(self):
        r = self.start()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["access_mode"], body["read_only"], body["grading_available"], body["max_cases"], body["remaining_cases"]), ("guest_view", True, False, 20, 20))
        cookies = r.headers.get_list("set-cookie")
        self.assertEqual(len(cookies), 1, cookies)   # 학습자 세션 쿠키를 만들지 않는다
        c = cookies[0].lower()
        self.assertTrue(c.startswith(guest.COOKIE_NAME + "="))
        for part in ("httponly", "samesite=lax", "path=/", "max-age=1800"):
            self.assertIn(part, c)
        self.assertNotIn("domain=", c)
        token = self.client.cookies.get(guest.COOKIE_NAME)
        self.assertGreaterEqual(len(token), 32)
        self.assertNotIn(token, r.text)                 # 식별값은 본문에 없다
        self.assertEqual(self.client.get("/_session").json().get("learner"), None)   # learner 세션 없음

    def test_ac02_reuse_keeps_lifetime_and_case_budget(self):
        self.started_case()
        self.clock.t += 600
        again = self.start().json()
        self.assertEqual(again["remaining_cases"], 19)
        self.assertLessEqual(again["expires_in"], 1200)
        self.assertEqual(len(self.store._states), 1)

    def test_ac02_expiry_tampering_and_issue_limit(self):
        self.started_case()
        self.clock.t += 1801
        r = self.random()
        self.assertEqual((r.status_code, r.json()["error"]), (401, "guest_session_expired"))
        bad = self.new_client()
        bad.cookies.set(guest.COOKIE_NAME, "forged-value")
        self.assertEqual(bad.get(f"{G}/random").json()["error"], "guest_session_required")
        self.store = guest.reset_store(guest.GuestLimits(issue_per_ip=2), clock=self.clock)
        for _ in range(2):
            self.assertEqual(self.start(self.new_client()).status_code, 200)
        r = self.start(self.new_client())
        self.assertEqual((r.status_code, r.json()["error"]), (429, "guest_rate_limited"))
        self.assertIn("retry-after", r.headers)
        spoof = self.new_client().post(f"{G}/session", headers={"Origin": FRONT, "X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(spoof.status_code, 429)   # X-Forwarded-For 로 우회되지 않는다

    def test_ac02_end_removes_only_guest_state(self):
        self.client.post("/_login", params={"idx": self.h.user_idx})
        self.started_case()
        r = self.client.post(f"{G}/session/end", headers={"Origin": FRONT})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store._states, {})
        self.assertEqual(self.random().json()["error"], "guest_session_required")
        self.assertIsNotNone(self.client.get("/_session").json().get("learner"))   # 학습자 세션은 그대로

    # ── AC03 저장·채점 0 ──
    def test_ac03_guest_cookie_gives_no_learner_rights_and_no_save(self):
        create = mock.MagicMock(side_effect=AssertionError("review_node 저장 금지"))
        with mock.patch.object(import_module("routers.kneeLearning").review_node_repositories, "create", create):
            idx, _ = self.started_case()
            self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": idx}).status_code, 401)
            g = self.client.post("/learning/roi-grade/knee", data={"study_idx": str(idx), "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.2", "height": "0.2", "slice_index": "12"})
            self.assertEqual(g.status_code, 401)
            self.assertEqual(self.client.get("/learning/knee/random").status_code, 401)
            self.assertEqual(self.client.get("/learning/knee/series", params={"study_idx": idx}).status_code, 401)
            for path in (f"{G}/grade", f"{G}/task", f"{G}/roi-grade"):
                self.assertIn(self.client.post(path, headers={"Origin": FRONT}).status_code, (404, 405))
        create.assert_not_called()
        self.assertEqual(len(self.h.reviews()), 0)

    # ── AC04 등록 범위 ──
    def test_ac04_random_only_registered_cases_minimal_response(self):
        self.store = guest.reset_store(guest.GuestLimits(meta_per_guest_per_min=10000, meta_per_ip_per_min=10000), clock=self.clock)   # 반복 확인용으로 메타 한도만 넉넉히
        self.start()
        seen = set()
        for _ in range(60):
            body = self.random().json()
            self.assertEqual(set(body), {"study_idx", "case_count", "same_as_excluded", "remaining_cases", "unavailable_reason"})
            self.assertEqual(body["case_count"], 3)
            seen.add(body["study_idx"])
        self.assertEqual(sorted(seen), self.ids)
        self.assertNotIn(self.h.unapproved_study_idx, seen)
        for cur in self.ids:
            self.assertNotIn(cur, {self.random(exclude=cur).json()["study_idx"] for _ in range(20)})
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {}, clear=True):
            body = self.random().json()
            self.assertEqual((body["study_idx"], body["unavailable_reason"]), (None, "no_registered_cases"))

    def test_ac04_series_has_real_count_and_no_internal_values(self):
        self.store = guest.reset_store(guest.GuestLimits(issue_per_ip=100), clock=self.clock)   # 세션 여러 개를 쓰는 확인용
        for _ in range(6):
            idx, body = self.started_case(self.new_client())
            self.assertEqual((body["slice_count"], len(body["slices"])), (self.counts[idx], self.counts[idx]))
            self.assertEqual((body["read_only"], body["grading_available"], body["representative_slice_index"], body["grading_enabled"]), (True, False, None, False))
            dumped = json.dumps(body, ensure_ascii=False)
            for secret in ("_knee_assets", ".png", "sha", "reference", "token", "registrar", "ACL"):
                self.assertNotIn(secret, dumped)
            self.assertTrue(all(s["image_url"].startswith(f"{G}/series/{idx}/{body['series_id']}/image/") for s in body["slices"]))

    # ── AC05 케이스·영상 결속 ──
    def test_ac05_only_granted_cases_and_images(self):
        self.start()
        for idx in self.ids:
            self.assertEqual(self.series(idx).json()["error"], "guest_case_not_granted")   # random 없이 직행 불가
        idx, body = self.started_case()
        img = self.client.get(body["slices"][0]["image_url"])
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.headers["content-type"], "image/png")
        self.assertEqual((img.headers["cache-control"], img.headers["x-content-type-options"]), ("no-store", "nosniff"))
        reg = series_assets.APPROVED_SERIES_ASSETS[idx][0]
        self.assertEqual(hashlib.sha256(img.content).hexdigest(), reg.slice_sha256[0])
        other = self.new_client()                       # 다른 세션: 같은 주소를 복사해도 허용 안 됨
        self.start(other)
        self.assertEqual(other.get(body["slices"][0]["image_url"]).status_code, 403)
        n = len(body["slices"])
        for bad in (str(n), "-1", "1.5", "x"):
            self.assertEqual(self.client.get(body["slices"][0]["image_url"].rsplit("/", 1)[0] + "/" + bad).status_code, 404, bad)
        wrong = body["slices"][0]["image_url"].replace(body["series_id"], "0" * 20)
        self.assertEqual(self.client.get(wrong).json()["error"], "guest_series_changed")

    def test_ac05_registration_change_requires_reopen(self):
        idx, body = self.started_case()
        url = body["slices"][1]["image_url"]
        row = series_assets.APPROVED_SERIES_ASSETS[idx][0]
        new_row = row.__class__(**{**row.__dict__, "version": "series-v9", "slice_sha256": row.slice_sha256})
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {idx: [new_row]}):
            # 같은 영상·버전만 바뀐 경우 series_id 는 같을 수 있다 → 영상은 계속 검증을 통과해야 한다
            self.assertIn(self.client.get(url).status_code, (200, 409))
        withdrawn = row.__class__(**{**row.__dict__, "status": "R"})
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {idx: [withdrawn]}):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertFalse(self.series(idx).json()["series_available"])

    # ── AC06 한도·동시성 ──
    def test_ac06_case_limit_boundary_and_no_double_count(self):
        self.store = guest.reset_store(guest.GuestLimits(max_cases=2), clock=self.clock)
        self.start()
        a = self.random().json()["study_idx"]
        self.series(a)
        self.series(a)                                   # 같은 케이스 재열람: 차감 없음
        b = self.random(exclude=a).json()
        self.assertEqual(b["remaining_cases"], 0)
        c = self.random(exclude=b["study_idx"]).json()   # 한도 소진: 이미 허용된 케이스 중에서만
        self.assertIn(c.get("study_idx"), {a, b["study_idx"]})
        state = next(iter(self.store._states.values()))
        self.assertEqual(len(state.grants), 2)

    def test_ac06_limit_reached_reopens_current_case_without_new_grant(self):
        """한도 1 을 다 쓴 뒤 '다른 케이스'(exclude=지금 케이스): 다른 허용 케이스가 없으면 지금 케이스를 다시 연다(same_as_excluded, 새 허용 없음)."""
        self.store = guest.reset_store(guest.GuestLimits(max_cases=1), clock=self.clock)
        self.start()
        a = self.random().json()["study_idx"]
        again = self.random(exclude=a)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual((again.json()["study_idx"], again.json()["same_as_excluded"], again.json()["remaining_cases"]), (a, True, 0))
        state = next(iter(self.store._states.values()))
        self.assertEqual(list(state.grants), [a])
        other = next(i for i in self.ids if i != a)
        r = self.random(exclude=other)                  # 지금 케이스가 허용되지 않은 다른 번호라면 허용된 a 로
        self.assertEqual(r.json()["study_idx"], a)

    def test_ac06_concurrent_random_never_exceeds_limit(self):
        self.store = guest.reset_store(guest.GuestLimits(max_cases=1, meta_per_guest_per_min=1000, meta_per_ip_per_min=1000), clock=self.clock)
        self.start()
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.random().status_code)) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        state = next(iter(self.store._states.values()))
        self.assertEqual(len(state.grants), 1)
        self.assertTrue(all(code in (200, 429) for code in results), results)

    def test_ac06_meta_and_image_rate_limits(self):
        self.store = guest.reset_store(guest.GuestLimits(meta_per_guest_per_min=3, image_guest_rate=1, image_guest_burst=2), clock=self.clock)
        idx, body = self.started_case()                  # random+series = 2 회
        self.assertEqual(self.series(idx).status_code, 200)
        r = self.series(idx)
        self.assertEqual((r.status_code, r.json()["error"]), (429, "guest_rate_limited"))
        self.assertIn("retry-after", r.headers)
        self.clock.t += 61
        self.assertEqual(self.series(idx).status_code, 200)
        url = body["slices"][0]["image_url"]
        self.assertEqual([self.client.get(url).status_code for _ in range(3)], [200, 200, 429])
        self.clock.t += 2
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_ac06_memory_cap(self):
        self.store = guest.reset_store(guest.GuestLimits(max_states=2, issue_per_ip=10), clock=self.clock)
        self.assertEqual([self.start(self.new_client()).status_code for _ in range(3)], [200, 200, 503])
        self.clock.t += 1801                             # 만료 정리 뒤에는 다시 발급
        self.assertEqual(self.start(self.new_client()).status_code, 200)

    # ── AC07 출처·노출 ──
    def test_ac07_origin_and_fetch_site_checks(self):
        self.assertEqual(self.start(origin=None).json()["error"], "guest_origin_forbidden")
        self.assertEqual(self.start(origin="http://evil.test").json()["error"], "guest_origin_forbidden")
        self.assertEqual(self.client.post(f"{G}/session/end").status_code, 403)
        self.started_case()
        self.assertEqual(self.client.get(f"{G}/random", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.get(f"{G}/random", headers={"Origin": "http://evil.test"}).status_code, 403)
        self.assertEqual(self.client.get(f"{G}/random", headers={"Origin": FRONT, "Sec-Fetch-Site": "same-origin"}).status_code, 200)

    def test_ac07_every_guest_error_and_validation_response_is_no_store(self):
        """FINDING-GUEST-002: 게스트 주소의 401·403·404·409·422·429·503(공용 영상 helper·입력 검증 포함) 모두 Cache-Control: no-store."""
        def no_store(r, status):
            self.assertEqual(r.status_code, status, r.text)
            self.assertEqual(r.headers.get("cache-control"), "no-store", (status, r.text))

        no_store(self.random(), 401)                                              # 세션 없음
        idx, body = self.started_case()
        base = body["slices"][0]["image_url"].rsplit("/", 1)[0]
        no_store(self.client.get(base + "/999"), 404)                             # 범위 밖 장(공용 helper 404)
        no_store(self.client.get(base + "/abc"), 404)
        no_store(self.client.get(body["slices"][0]["image_url"].replace(body["series_id"], "0" * 20)), 409)
        no_store(self.client.get(f"{G}/series", params={"study_idx": "no"}), 422)   # 입력 검증
        no_store(self.client.get(f"{G}/series"), 422)
        no_store(self.client.get(f"{G}/random", params={"exclude": "abc"}), 422)
        no_store(self.client.get(f"{G}/series/x/{body['series_id']}/image/0"), 422)
        no_store(self.series(self.h.unapproved_study_idx), 403)                   # 허용 안 된 케이스
        no_store(self.client.get(f"{G}/random", headers={"Origin": "http://evil.test"}), 403)
        with mock.patch.object(import_module("routers.kneeLearning"), "_image_source", return_value=None):
            no_store(self.series(idx), 503)                                       # 공급원 없음(게스트 응답)
            no_store(self.client.get(body["slices"][1]["image_url"]), 503)        # 공용 helper 503
        with mock.patch.dict(os.environ, {"KNEE_GUEST_VIEW_ENABLED": "0"}):
            no_store(self.random(), 503)
        self.store = guest.reset_store(guest.GuestLimits(meta_per_guest_per_min=1), clock=self.clock)
        self.start()
        self.random()
        no_store(self.random(), 429)
        # 학습자 주소의 응답 계약은 바뀌지 않는다(공용 처리기 그대로)
        self.assertEqual(self.client.get("/learning/knee/series", params={"study_idx": "no"}).status_code, 422)

    def test_ac07_unicode_digit_like_inputs_are_422_no_store_not_500(self):
        """FINDING-GUEST-002 잔여: '²'·전각·아랍 숫자처럼 isdigit() 은 True 지만 int() 가 실패하는 값과 그 밖의 비ASCII·형식 오류는
        study_idx·exclude·경로 번호 모두 422 guest_bad_request + no-store(세션 확인보다 먼저라 쿠키 없이도 같다)."""
        idx, body = self.started_case()
        sid = body["series_id"]
        bad_values = ["²", "１", "١", "³4", "1²", "-1", "+1", "1.0", "1e3", "0x1", "１２", "1234567890"]
        for v in bad_values:
            cases = [
                self.client.get(f"{G}/series", params={"study_idx": v}),
                self.client.get(f"{G}/random", params={"exclude": v}),
                self.client.get(f"{G}/series/{quote(v, safe='')}/{sid}/image/0"),
            ]
            for r in cases:
                self.assertEqual(r.status_code, 422, (v, r.request.url, r.text))
                self.assertEqual(r.headers.get("cache-control"), "no-store", (v, r.request.url))
                self.assertEqual(r.json()["error"], "guest_bad_request")
        anon = self.new_client()                       # 세션 없이도 500 이 아니라 422
        self.assertEqual(anon.get(f"{G}/series", params={"study_idx": "²"}).status_code, 422)
        # 정상 ASCII 숫자(앞뒤 공백 포함)는 그대로 동작
        self.assertEqual(self.client.get(f"{G}/series", params={"study_idx": f" {idx} "}).status_code, 200)
        self.assertEqual(self.client.get(f"{G}/random", params={"exclude": str(idx)}).status_code, 200)
        self.assertEqual(self.client.get(f"{G}/series/{idx}/{sid}/image/0").status_code, 200)

    def test_ac07_no_cookie_value_in_logs(self):
        records = []
        handler = logging.Handler()
        handler.emit = lambda rec: records.append(rec.getMessage())
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            idx, body = self.started_case()
            self.client.get(body["slices"][0]["image_url"])
            self.client.get(f"{G}/series", params={"study_idx": 99999})
        finally:
            root.removeHandler(handler)
        token = self.client.cookies.get(guest.COOKIE_NAME)
        self.assertFalse(any(token in m for m in records))


if __name__ == "__main__":
    unittest.main()
