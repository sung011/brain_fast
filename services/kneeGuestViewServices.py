"""
무릎 MRI **게스트 열람**(로그인 없이 실제 영상 탐색·박스 연습만) 세션·제한 서비스. 설계: designs/MSN-20261016-002/DEMO_REAL_VIEW_PLAN.md 4·5장.

- 게스트는 학습자가 아니다: 기존 세션(learner/user)과 분리된 **독립 HttpOnly 쿠키**의 무작위 식별값으로만 구분하고, 상태는 이 프로세스 메모리에만 둔다
  (DB·응답 본문·로그에 식별값을 남기지 않는다). 서버를 다시 켜면 모든 게스트 상태가 사라진다.
- 채점·풀이 저장은 없다. 이 서비스는 "어느 케이스를 볼 수 있는가"와 "얼마나 자주 요청하는가"만 판단한다.
- 제한(발표용 단일 프로세스 기준): 절대 수명 30분(읽어도 연장 안 함), 세션당 서로 다른 케이스 20개, 세션 발급 IP당 10분 5회,
  메타데이터 요청 게스트당 분당 60·IP당 180, 영상 요청 토큰 버킷 게스트 초당 20(버스트 80)·IP 초당 60(버스트 180), 전체 게스트 상태 256개.
  **대량 수집을 완전히 막지는 못한다**(쿠키·IP 를 바꾸면 우회 가능, 화면 영상은 저장 가능). 발표 중 실수·간단한 자동 반복을 줄이는 수준이다.
- 다중 프로세스/서버에서는 한도가 공유되지 않는다(그 환경에서는 게스트를 끄거나 공유 저장소를 도입해야 한다).
DB·NAS·모델을 쓰지 않는다. 시계·난수는 시험에서 주입할 수 있다(운영 우회 주소는 없다).
"""
from __future__ import annotations

import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

COOKIE_NAME = "mediscan_knee_guest"
SCOPE = "knee-series-view"


@dataclass(frozen=True)
class GuestLimits:
    session_ttl_s: int = 1800
    max_cases: int = 20
    issue_per_ip: int = 5
    issue_window_s: int = 600
    meta_per_guest_per_min: int = 60
    meta_per_ip_per_min: int = 180
    image_guest_rate: float = 20.0
    image_guest_burst: float = 80.0
    image_ip_rate: float = 60.0
    image_ip_burst: float = 180.0
    max_states: int = 256
    max_ip_entries: int = 4096
    random_attempts: int = 12


class GuestError(Exception):
    """게스트 요청을 거절한다. code 는 화면이 고르는 고정 사유, retry_after 는 초(429 일 때)."""

    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass
class _Bucket:
    tokens: float
    updated: float

    def take(self, now: float, rate: float, burst: float) -> float:
        """토큰 하나를 쓴다. 쓸 수 있으면 0, 아니면 다음 토큰까지 기다릴 초."""
        self.tokens = min(burst, self.tokens + (now - self.updated) * rate)
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / rate


@dataclass
class GuestState:
    created: float
    expires: float
    grants: dict = field(default_factory=dict)   # study_idx → series_id(이 세션에 허용된 케이스와 그때의 시리즈 식별값)
    meta: deque = field(default_factory=deque)
    image: _Bucket | None = None
    scope: str = SCOPE


class GuestStore:
    def __init__(self, limits: GuestLimits | None = None, *, clock=time.monotonic, token_factory=None):
        self.limits = limits or GuestLimits()
        self._clock = clock
        self._token = token_factory or (lambda: secrets.token_urlsafe(32))
        self._lock = threading.Lock()
        self._states: dict[str, GuestState] = {}
        self._ip_issue: dict[str, deque] = {}
        self._ip_meta: dict[str, deque] = {}
        self._ip_image: dict[str, _Bucket] = {}

    # ── 정리 ──
    def _cleanup(self, now: float) -> None:
        for token in [t for t, s in self._states.items() if s.expires <= now]:
            del self._states[token]
        for table, window in ((self._ip_issue, self.limits.issue_window_s), (self._ip_meta, 60)):
            for ip in list(table):
                q = table[ip]
                while q and q[0] <= now - window:
                    q.popleft()
                if not q:
                    del table[ip]
        for ip in [ip for ip, b in self._ip_image.items() if now - b.updated > 60]:
            del self._ip_image[ip]

    def _ip_table_full(self, table) -> bool:
        return len(table) >= self.limits.max_ip_entries

    # ── 세션 ──
    def issue(self, ip: str) -> tuple[str, GuestState]:
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            q = self._ip_issue.get(ip)
            if q is None:
                if self._ip_table_full(self._ip_issue):
                    raise GuestError(503, "guest_capacity", "지금은 게스트 열람을 더 시작할 수 없어요.")
                q = self._ip_issue[ip] = deque()
            if len(q) >= self.limits.issue_per_ip:
                wait = math.ceil(q[0] + self.limits.issue_window_s - now)
                raise GuestError(429, "guest_rate_limited", "게스트 열람을 너무 자주 시작했어요.", max(1, wait))
            if len(self._states) >= self.limits.max_states:
                raise GuestError(503, "guest_capacity", "지금은 게스트 열람을 더 시작할 수 없어요.")
            token = self._token()
            state = GuestState(created=now, expires=now + self.limits.session_ttl_s,
                               image=_Bucket(self.limits.image_guest_burst, now))
            self._states[token] = state
            q.append(now)
            return token, state

    def get(self, token: str | None) -> GuestState:
        now = self._clock()
        with self._lock:
            state = self._states.get(token) if isinstance(token, str) and token else None
            if state is None:
                raise GuestError(401, "guest_session_required", "게스트 열람을 먼저 시작해 주세요.")
            if state.expires <= now or state.scope != SCOPE:
                self._states.pop(token, None)
                raise GuestError(401, "guest_session_expired", "게스트 열람 시간이 끝났어요.")
            return state

    def end(self, token: str | None) -> None:
        with self._lock:
            if isinstance(token, str):
                self._states.pop(token, None)

    def remaining(self, state: GuestState) -> dict:
        now = self._clock()
        return {"expires_in": max(0, int(state.expires - now)), "max_cases": self.limits.max_cases,
                "remaining_cases": max(0, self.limits.max_cases - len(state.grants))}

    # ── 요청 제한 ──
    def check_meta(self, state: GuestState, ip: str) -> None:
        now = self._clock()
        with self._lock:
            for q, limit in ((state.meta, self.limits.meta_per_guest_per_min), (self._ip_meta.get(ip), self.limits.meta_per_ip_per_min)):
                if q is None:
                    continue
                while q and q[0] <= now - 60:
                    q.popleft()
                if len(q) >= limit:
                    raise GuestError(429, "guest_rate_limited", "요청이 너무 많아요. 잠시 뒤 다시 시도해 주세요.", max(1, math.ceil(q[0] + 60 - now)))
            ipq = self._ip_meta.get(ip)
            if ipq is None:
                if self._ip_table_full(self._ip_meta):
                    self._cleanup(now)
                if self._ip_table_full(self._ip_meta):
                    raise GuestError(503, "guest_capacity", "지금은 요청을 처리할 수 없어요.")
                ipq = self._ip_meta[ip] = deque()
            state.meta.append(now)
            ipq.append(now)

    def check_image(self, state: GuestState, ip: str) -> None:
        now = self._clock()
        with self._lock:
            ip_bucket = self._ip_image.get(ip)
            if ip_bucket is None:
                if self._ip_table_full(self._ip_image):
                    self._cleanup(now)
                if self._ip_table_full(self._ip_image):
                    raise GuestError(503, "guest_capacity", "지금은 요청을 처리할 수 없어요.")
                ip_bucket = self._ip_image[ip] = _Bucket(self.limits.image_ip_burst, now)
            # 두 버킷을 모두 확인한 뒤에만 차감한다(한쪽만 깎이지 않게)
            g = _Bucket(state.image.tokens, state.image.updated)
            i = _Bucket(ip_bucket.tokens, ip_bucket.updated)
            wait_g = g.take(now, self.limits.image_guest_rate, self.limits.image_guest_burst)
            wait_i = i.take(now, self.limits.image_ip_rate, self.limits.image_ip_burst)
            if wait_g or wait_i:
                raise GuestError(429, "guest_rate_limited", "영상 요청이 너무 많아요. 잠시 뒤 다시 시도해 주세요.", max(1, math.ceil(max(wait_g, wait_i))))
            state.image, self._ip_image[ip] = g, i

    # ── 케이스 허용 ──
    def grant(self, state: GuestState, study_idx: int, series_id: str) -> None:
        """케이스를 이 세션에 허용한다. 이미 허용된 케이스는 한도를 다시 쓰지 않는다. 새 케이스가 한도를 넘으면 429."""
        with self._lock:
            if study_idx in state.grants:
                state.grants[study_idx] = series_id
                return
            if len(state.grants) >= self.limits.max_cases:
                raise GuestError(429, "guest_case_limit", "이번 게스트 열람에서 볼 수 있는 케이스 수를 다 썼어요.",
                                 max(1, int(state.expires - self._clock())))
            state.grants[study_idx] = series_id

    def granted_series(self, state: GuestState, study_idx: int) -> str | None:
        with self._lock:
            return state.grants.get(study_idx)

    def can_grant_new(self, state: GuestState) -> bool:
        with self._lock:
            return len(state.grants) < self.limits.max_cases


_store = GuestStore()


def store() -> GuestStore:
    return _store


def reset_store(limits: GuestLimits | None = None, *, clock=time.monotonic, token_factory=None) -> GuestStore:
    """시험·서버 시작용: 모든 게스트 상태를 비우고 새 저장소를 만든다(운영 HTTP 주소로는 부르지 않는다)."""
    global _store
    _store = GuestStore(limits, clock=clock, token_factory=token_factory)
    return _store


# ── 설정(비밀 아님) ──
def enabled(get_setting) -> bool:
    return get_setting("KNEE_GUEST_VIEW_ENABLED", "0") == "1"


def allowed_origins(get_setting) -> frozenset:
    """정확히 일치해야 하는 프런트 Origin 목록(쉼표). `*`·경로·공백 섞인 값은 무시한다."""
    raw = get_setting("KNEE_GUEST_ALLOWED_ORIGINS", "") or ""
    out = set()
    for item in raw.split(","):
        item = item.strip()
        if item and item != "*" and item.startswith(("http://", "https://")) and item.count("/") == 2 and " " not in item:
            out.add(item.rstrip("/"))
    return frozenset(out)


def cookie_secure(get_setting) -> bool:
    """기본 Secure(HTTPS). 발표용 HTTP 에서만 KNEE_GUEST_COOKIE_SECURE=0 으로 명시해 끈다."""
    return get_setting("KNEE_GUEST_COOKIE_SECURE", "1") != "0"
