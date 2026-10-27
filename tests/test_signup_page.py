"""
회원가입 화면(GET /signup) 테스트.

화면만 있고 실제 가입 API는 없다. 그래서 확인하는 것은
1) 화면이 돌아오는지, 미리보기는 ?preview=1 일 때만 켜지는지
2) 정적 파일(CSS/JS)이 서버에서 나가는지
3) 화면과 스크립트가 서버로 개인정보를 보내거나 저장하지 않는지
4) 관리자 가입 API의 보호를 건드리지 않았는지
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from routers.user import router as user_router

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "assets" / "js" / "signup.js").read_text(encoding="utf-8")
CSS = (ROOT / "assets" / "css" / "signup.css").read_text(encoding="utf-8")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(user_router)
    app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
    return app


class SignupPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_app())

    def test_live_page_has_required_fields_and_no_admin_or_completion(self) -> None:
        response = self.client.get("/signup")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        html = response.text
        for field_id in ("email", "password", "confirm", "nickname", "birth", "school", "major"):
            self.assertIn(f'id="{field_id}"', html)
        for option in ("방사선학과", "의학과/의예과", "간호학과", "기타 보건의료계열", "비전공/해당 없음"):
            self.assertIn(option, html)
        self.assertIn('data-mode="live"', html)
        # 운영 화면에는 설계 도구와 가짜 완료 화면이 없다.
        self.assertNotIn('id="scenario"', html)
        self.assertNotIn('id="stage3"', html)
        self.assertNotIn("회원가입이 완료됐어요", html)
        # 가입 경로, 닉네임 중복 확인은 없다.
        self.assertNotIn("가입 경로", html)
        self.assertNotIn("중복 확인", html)

    def test_preview_only_with_explicit_flag(self) -> None:
        on = self.client.get("/signup?preview=1").text
        self.assertIn('data-mode="preview"', on)
        self.assertIn('id="scenario"', on)
        self.assertIn('id="stage3"', on)
        self.assertIn("실제 계정 생성과 이메일 발송은 하지 않습니다", on)
        for value in ("0", "no", "abc", ""):
            off = self.client.get(f"/signup?preview={value}")
            self.assertEqual(off.status_code, 200)
            self.assertIn('data-mode="live"', off.text)

    def test_static_files_are_served(self) -> None:
        html = self.client.get("/signup").text
        self.assertIn('href="/assets/css/signup.css"', html)
        self.assertIn('src="/assets/js/signup.js"', html)
        css = self.client.get("/assets/css/signup.css")
        js = self.client.get("/assets/js/signup.js")
        self.assertEqual(css.status_code, 200)
        self.assertEqual(js.status_code, 200)
        self.assertIn("javascript", js.headers["content-type"])

    def test_html_page_is_not_in_api_docs(self) -> None:
        schema = self.client.get("/openapi.json").json()
        signup = schema["paths"].get("/signup", {})
        self.assertNotIn("get", signup)
        self.assertIn("post", signup)

    def test_inputs_have_no_name_attribute_so_values_never_go_to_url(self) -> None:
        html = self.client.get("/signup").text
        for tag in re.findall(r"<(?:input|select)\b[^>]*>", html):
            self.assertNotRegex(tag, r"\sname=", tag)
        for form in re.findall(r"<form\b[^>]*>", html):
            self.assertNotRegex(form, r"\saction=", form)

    def test_every_field_has_a_label(self) -> None:
        html = self.client.get("/signup").text
        for field_id in ("email", "password", "confirm", "nickname", "birth", "school", "major"):
            self.assertRegex(html, rf'<label class="title" for="{field_id}"')

    def test_no_external_resources(self) -> None:
        html = self.client.get("/signup?preview=1").text
        for text in (html, CSS, JS):
            self.assertNotRegex(text, r"(?:src|href)=[\"']https?://")
            self.assertNotIn("@import", text)
            self.assertNotRegex(text, r"url\(\s*[\"']?https?://")


class SignupScriptSafetyTests(unittest.TestCase):
    def test_script_sends_and_stores_nothing(self) -> None:
        code = re.sub(r"/\*.*?\*/", "", JS, flags=re.S)
        code = re.sub(r"(?m)^\s*//.*$", "", code)
        for banned in (
            "fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource",
            "localStorage", "sessionStorage", "indexedDB", "document.cookie",
            "console.", "location.", "history.", "innerHTML = value", ".submit(",
            "/admin", "check-id",
        ):
            self.assertNotIn(banned, code, f"스크립트에 {banned!r} 가 있으면 안 됩니다")

    def test_inner_html_is_only_used_for_fixed_button_text(self) -> None:
        uses = re.findall(r"innerHTML\s*=\s*([^;]+);", JS)
        for expression in uses:
            self.assertRegex(expression.strip(), r"^'[^'$]*'$", "고정 문자열만 innerHTML 에 넣을 수 있습니다")

    def test_completion_screen_is_preview_only(self) -> None:
        # 운영 모드에서는 제출해도 완료 화면(show(3))으로 가지 못한다.
        live_branch = JS.split("if (!PREVIEW) {\n      $('liveNotice')")[1].split("return;")[0]
        self.assertNotIn("show(3)", live_branch)
        self.assertIn("준비되지 않았습니다", live_branch)


class SignupNoticeAndMajorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_app())

    def test_live_page_shows_not_connected_notice_before_any_input(self) -> None:
        html = self.client.get("/signup").text
        notice = html.index('id="demoNotice"')
        # 안내는 첫 입력칸과 두 폼보다 앞에 있고, 숨김 상태가 아니다.
        self.assertLess(notice, html.index('id="email"'))
        self.assertLess(notice, html.index('id="accountForm"'))
        self.assertLess(notice, html.index('id="profileForm"'))
        tag = re.search(r'<div[^>]*id="demoNotice"[^>]*>', html).group(0)
        self.assertNotIn("hidden", tag)
        self.assertIn('role="note"', tag)
        # 머리말 바로 아래 띠라서 소개·폼보다 먼저 나온다(작은 화면 첫 화면에서도 보이게).
        self.assertLess(html.index("</header>"), notice)
        self.assertLess(notice, html.index("<main>"))
        self.assertIn("실제 가입·전송·저장 없음", html)
        self.assertIn("실제 개인정보는 입력하지 마세요", html)
        # 두 단계가 같은 안내 아래에 있다(안내가 폼 안쪽 특정 단계에만 있지 않다).
        self.assertNotIn('id="demoNotice"', html[html.index('id="stage1"'):])
        self.assertIn("<noscript>", html)

    def test_preview_uses_toolbar_instead_of_notice(self) -> None:
        html = self.client.get("/signup?preview=1").text
        self.assertNotIn('id="demoNotice"', html)
        self.assertIn("실제 가입·메일 발송 없음", html)

    def test_service_items_are_only_shown_on_step_two_by_css(self) -> None:
        for path in ("/signup", "/signup?preview=1"):
            html = self.client.get(path).text
            self.assertIn('data-stage="1"', html)  # 처음엔 1단계 소개(그림·흐름)
            items = re.findall(r'<li>\s*<span class="ficon"', html)
            self.assertEqual(len(items), 4)
            for title in ("영상 보고 문제 풀기", "관심 영역 표시와 복습", "AI 참고 분석", "용어 사전과 질문"):
                self.assertIn(f"<strong>{title}</strong>", html)
            self.assertIn("진단이 아닙니다", html)
        self.assertRegex(CSS, r"\.features \{ display: none;")
        self.assertIn("body[data-stage='2'] .features { display: grid; }", CSS)
        self.assertIn("body[data-stage='2'] .art, body[data-stage='2'] .flow { display: none; }", CSS)
        self.assertRegex(CSS, r"@media \(max-width: 700px\) \{ body\[data-stage='2'\] \.features \{ display: none; \} \}")

    def test_welcome_text_exists_only_in_preview_and_replaces_intro_on_step_three(self) -> None:
        on = self.client.get("/signup?preview=1").text
        self.assertIn('class="intro-welcome"', on)
        welcome = on[on.index('class="intro-welcome"'):]
        self.assertIn("환영합니다", welcome)
        # 운영 화면에는 완료 단계가 없으므로 환영 문구도 없다.
        self.assertNotIn("intro-welcome", self.client.get("/signup").text)
        self.assertRegex(CSS, r"\.intro-welcome \{ display: none; \}")
        self.assertIn("body[data-stage='3'] .intro-welcome { display: block; }", CSS)
        self.assertIn("body[data-stage='3'] .intro-main { display: none; }", CSS)
        self.assertIn("body[data-stage='3'] .flow { display: none; }", CSS)

    def test_length_limits_are_visible_next_to_labels_and_highlighted_at_limit(self) -> None:
        html = self.client.get("/signup").text
        # 압축 화면에서는 힌트 문장이 숨겨지므로 라벨 옆 글씨로 상한을 항상 보여 준다.
        for field, suffix in (
            ("email", "최대 30자"),
            ("nickname", "1~10자"),
            ("school", "최대 20자"),
            ("major", "최대 20자"),
        ):
            label = re.search(rf'<label class="title" for="{field}".*?</label>', html, flags=re.S).group(0)
            self.assertRegex(label, rf'<small class="lsuffix" id="\w+Limit" aria-hidden="true">[^<]*{re.escape(suffix)}[^<]*</small>')
        self.assertIn("(최대 20자)", re.search(r'<input[^>]*id="majorOther"[^>]*>', html).group(0))
        # 접근성용 힌트 문장과 연결은 그대로 둔다.
        for hint in ("emailHint", "nickHint", "schoolHint", "majorHint"):
            self.assertIn(f'id="{hint}"', html)
        # 한도에 닿으면 라벨 옆 글씨를 강조하는 규칙이 압축 화면에 있다.
        self.assertIn(".lsuffix.show-limit { color: var(--mint);", CSS)
        self.assertNotIn(".hint.show-limit", CSS)  # 줄을 새로 끼워 높이를 바꾸는 방식은 쓰지 않는다
        for mark in ("emailLimit", "nickLimit", "schoolLimit", "majorLimit"):
            self.assertIn(f'id="{mark}"', html)
            self.assertIn(mark, JS)

    def test_mobile_layout_puts_the_form_first(self) -> None:
        start = CSS.index("@media (max-width: 700px) {\n  header {")
        block = CSS[start:CSS.index("\n}\n", start)]
        # 소개는 한 줄 태그라인만 남기고 큰 소개(제목 위 문구·설명·그림·흐름)는 숨긴다.
        self.assertIn(".intro .eyebrow, .intro .lead, .intro .art, .intro .flow, .intro small, .features { display: none; }", block)
        self.assertIn(".intro h1 { font-size: 15px;", block)
        # 가입 폼은 카드, 보조 설명은 숨겨 폼을 첫 화면으로 올린다.
        self.assertRegex(block, r"\.form-shell \{[^}]*border-radius: 16px;")
        self.assertIn(".sub { display: none; }", block)
        # iOS 자동 확대 방지(입력칸 16px 이상)와 터치 크기.
        self.assertIn("input:not([type='checkbox']), select { height: 46px; font-size: 16px; }", block)
        self.assertIn(".check input { width: 20px; height: 20px; }", block)
        # 상한 강조는 모바일에서도 동작한다.
        self.assertIn(".lsuffix.show-limit::after { content: ' · 한도 도달'; }", block)

    def test_major_fields_are_linked_to_error_message(self) -> None:
        html = self.client.get("/signup").text
        select = re.search(r'<select[^>]*id="major"[^>]*>', html).group(0)
        other = re.search(r'<input[^>]*id="majorOther"[^>]*>', html).group(0)
        for tag in (select, other):
            self.assertIn("majorError", re.search(r'aria-describedby="([^"]*)"', tag).group(1))
        self.assertIn('id="majorError"', html)
        self.assertIn('aria-live="polite"', re.search(r'<p[^>]*id="majorError"[^>]*>', html).group(0))


class SignupBehaviorScriptTests(unittest.TestCase):
    """signup.js 를 Node 가짜 DOM 에서 실행한다(브라우저 렌더링·IME·Tab 순서는 확인하지 않음)."""

    def test_dom_behavior_checks_pass(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 가 없어 화면 동작 검사를 건너뜀")
        script = ROOT / "tests" / "signup_dom_check.cjs"
        result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertIn("0 failed", result.stdout)


class AdminSignupStaysProtectedTests(unittest.TestCase):
    def test_admin_signup_still_requires_admin_session(self) -> None:
        try:
            from routers.admin import router as admin_router
        except Exception as exc:  # 이 환경에 관리자 라우터 의존성(DB 등)이 없으면 건너뜀
            self.skipTest(f"관리자 라우터를 불러올 수 없음: {exc}")
        from starlette.middleware.sessions import SessionMiddleware

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-only-secret")  # 실제 앱과 같은 세션 기능
        app.include_router(admin_router)
        client = TestClient(app, follow_redirects=False)
        for method, path in (
            ("get", "/admin/user/signup"),
            ("post", "/admin/user/signup"),
            ("get", "/admin/user/check-id?user_id=a@b.co"),
        ):
            response = getattr(client, method)(path)
            # 로그인 없이는 로그인 화면으로 돌려보낸다(303). 200 이면 보호가 풀린 것이다.
            self.assertEqual(response.status_code, 303, f"{method.upper()} {path} 가 보호되지 않습니다")
            self.assertIn("/admin/login", response.headers.get("location", ""))

    def test_user_router_does_not_touch_admin_paths(self) -> None:
        paths = {route.path for route in user_router.routes}
        self.assertFalse([p for p in paths if p.startswith("/admin")])


if __name__ == "__main__":
    unittest.main()
