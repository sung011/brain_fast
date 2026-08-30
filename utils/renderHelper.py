"""
지정된 뷰를 레이아웃과 함께 렌더링하는 헬퍼.

Express의 renderWithLayout(res, next, view, data) 와 같은 흐름이다.
1) 콘텐츠 뷰를 HTML 문자열로 렌더링한다
2) 그 HTML을 body 에 넣고 레이아웃(partials/layout_adm.html)을 렌더링한다
에러가 나면 FastAPI 예외 핸들러로 넘어간다 (Express의 next(err) 역할).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.responses import HTMLResponse

KST = timezone(timedelta(hours=9))
_WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


@dataclass(frozen=True)
class NowKo:
    """EJS now.toLocaleString('ko-KR', ...) 결과에 해당하는 한국어 날짜/시각."""

    weekday: str  # 화요일
    date: str  # 2026년 8월 25일
    time: str  # 오후 3:53


def now_ko(when: datetime | None = None) -> NowKo:
    """한국 시간 기준으로 요일·날짜·시각 문자열을 만든다."""
    dt = when or datetime.now(KST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    else:
        dt = dt.astimezone(KST)

    hour12 = dt.hour % 12 or 12
    period = "오전" if dt.hour < 12 else "오후"
    return NowKo(
        weekday=_WEEKDAYS_KO[dt.weekday()],
        date=f"{dt.year}년 {dt.month}월 {dt.day}일",
        time=f"{period} {hour12}:{dt.minute:02d}",
    )

# 관리자 공통 레이아웃. Express의 'partials/layout_adm' 에 해당한다.
DEFAULT_LAYOUT = "partials/layout_adm.html"


def _view_name(view: str) -> str:
    """'index' 처럼 확장자가 없으면 .html 을 붙인다. Express의 'adm/index' 와 비슷하게 쓰기 위함."""
    if view.endswith(".html"):
        return view
    return f"{view}.html"


def render_with_layout(
    request: Request,
    templates: Jinja2Templates,
    view: str,
    data: dict[str, Any] | None = None,
    layout: str = DEFAULT_LAYOUT,
) -> HTMLResponse:
    """
    콘텐츠 뷰를 레이아웃과 함께 렌더링한다.

    :param request: FastAPI Request. 템플릿에서 request 를 쓸 수 있게 넘긴다.
    :param templates: Jinja2Templates 인스턴스
    :param view: 콘텐츠 뷰 경로 (예: 'index' 또는 'index.html')
    :param data: 뷰에 전달할 데이터 (예: {"title": "페이지 제목"})
    :param layout: 레이아웃 템플릿. 기본값은 partials/layout_adm.html
    """
    context = dict(data or {})
    context["request"] = request
    # Express에서 now 를 뷰에 넘기던 것과 같다. 호출측에서 now 를 주면 그대로 쓴다.
    context.setdefault("now", now_ko())
    context.setdefault("user", request.session.get("user"))

    # 1) 콘텐츠 뷰를 HTML 문자열로 렌더링한다.
    html = templates.env.get_template(_view_name(view)).render(context)

    # 2) 렌더링된 HTML 을 body 에 담고, 원본 데이터도 레이아웃에 함께 넘긴다.
    #    Markup: Jinja가 HTML 태그를 이스케이프하지 않게 한다.
    layout_context = {**context, "body": Markup(html)}
    return templates.TemplateResponse(
        request=request,
        name=_view_name(layout),
        context=layout_context,
    )
