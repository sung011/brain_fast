/*
 * 무릎 ROI 학습 화면 (GET /learn/knee). 대표 영상 1장에 사각형 박스를 그려 서버에 제출하고 결과를 보여 준다.
 *
 * 이 파일이 하는 일: 영상 표시, 박스 그리기·이동·크기 조절, 제출, 결과 표시, 다시 풀기, 문제 열기 실패 뒤 다시 시도.
 * 이 파일이 하지 않는 일: 채점 계산, 기준 박스 보관, 정답 판정. 그것은 서버(POST /learning/roi-grade/knee)만 한다.
 * 좌표는 "실제 영상이 그려진 상자"(#canvasWrap)를 기준으로 0~1 로 바꿔 보낸다(확대·여백·화면 크기와 무관, knee-learning-core.js).
 *
 * 두 가지 연결 방식(body[data-mode]):
 *  - demo: 합성 영상과 시험용 고정 응답. 서버에 아무것도 보내지 않는다(단계 A, 실제 채점 아님).
 *  - live: GET /learning/knee/task 로 문제를 열고 POST /learning/roi-grade/knee 로 제출한다.
 *          상대 주소 + credentials:same-origin 이므로 화면과 서버가 **같은 Origin**(scheme+host+port)이어야 쿠키 세션이 간다.
 *          CSRF·Origin 검증 정책은 팀장의 인증 설계 전까지 완성되지 않았다(이 화면이 막아 주지 않는다).
 *
 * 박스 초안(draft) 규칙 — 문제를 다시 열어도(만료·오류·재시도) 박스는 메모리에 보관한다.
 *  - 다시 연 영상이 "같은 영상"(서버의 불투명 presentation_id·대표 번호·크기가 같음)이면 박스를 그대로 돌려준다.
 *  - 대표 영상이 바뀌었거나 같은 영상인지 확인할 수 없으면 이전 박스를 새 영상에 올리지 않는다. 변경 안내와 함께 보관만 하고,
 *    "새로 그리기" 또는 "이전 박스를 이 영상에 사용(직접 확인)"을 누르기 전에는 제출할 수 없다.
 *
 * 제출 결과를 못 받은 경우(통신 실패)에는 서버가 이미 처리했는지 알 수 없다. 다시 제출하면 기록이 한 번 더 남을 수 있다
 * (토큰 재사용은 별도 학습 시도를 허용하는 정책이며 중복 제거 키가 아니다). 화면은 "저장되지 않았다"고 단정하지 않는다.
 *
 * 저장하지 않는 것: 박스·토큰·응답을 localStorage/sessionStorage/주소에 남기지 않는다. 서버가 준 문자열은 textContent 로만 넣는다.
 */
(function () {
  "use strict";

  const Core = window.KneeLearningCore;
  const body = document.body;
  const MODE = body.dataset.mode === "demo" ? "demo" : "live";
  const STUDY_IDX = /^[1-9][0-9]{0,9}$/.test(body.dataset.study || "") ? Number(body.dataset.study) : null;
  const REQUEST_TIMEOUT_MS = 20000;
  const IMAGE_TIMEOUT_MS = 20000;
  const ZOOM_STEPS = [1, 1.25, 1.5, 2, 3];
  const BASIS_TEXT = {
    demo: "시연 영상은 합성 이미지이며 검수된 기준이 아닙니다.",
    pending: "기준 영역은 팀원의 라벨 확인을 마친 문제에만 제공됩니다.",
    reviewed: "팀원이 데이터셋의 위치 라벨을 확인했습니다.",
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    stage: $("stage"), stageMessage: $("stageMessage"), stageRetry: $("stageRetry"), btnStageRetry: $("btnStageRetry"),
    wrap: $("canvasWrap"), img: $("studyImage"),
    refRect: $("referenceRect"), userBox: $("userBox"), sliceInfo: $("sliceInfo"),
    changeNotice: $("changeNotice"), changeNoticeText: $("changeNoticeText"), btnUseOldBox: $("btnUseOldBox"), btnRedraw: $("btnRedraw"),
    zoomIn: $("zoomIn"), zoomOut: $("zoomOut"), zoomLabel: $("zoomLabel"),
    btnDefault: $("btnDefaultBox"), btnReset: $("btnReset"), btnSubmit: $("btnSubmit"), hint: $("drawHint"),
    card: $("resultCard"), verdict: $("resultVerdict"), iou: $("resultIou"), iouValue: $("resultIouValue"),
    rule: $("resultRule"), legend: $("resultLegend"), message: $("resultMessage"), notices: $("resultNotices"),
    btnRetry: $("btnRetry"), btnReopen: $("btnReopen"), scenario: $("scenario"), basisReview: $("basisReview"),
    loginForm: $("loginForm"), loginId: $("loginId"), loginPw: $("loginPw"), loginMessage: $("loginMessage"), btnLogin: $("btnLogin"),
  };

  const state = {
    task: null,          // 열린 문제(Core.interpretTask 결과, usable)
    asset: null,         // 지금 보이는 영상의 동일성(Core.assetIdentity)
    box: null,           // 사용자 박스(0~1, 왼쪽 위 기준)
    boxAsset: null,      // 그 박스를 그린 영상의 동일성. 지금 영상(asset)과 같은 객체일 때만 제출할 수 있다
    stash: null,         // 영상이 바뀌어 자동으로 옮기지 않고 보관만 한 이전 박스 {box, asset}
    locked: false,       // 채점 결과를 보는 동안 박스를 고정한다(다시 풀기로 해제)
    busy: false,         // 제출 진행 중(중복 제출 방지)
    opening: false,      // 문제 열기 진행 중(중복 열기·경쟁 응답 방지)
    loginBusy: false,    // 로그인 요청 진행 중(중복 방지)
    openSeq: 0,          // 가장 최근 열기 요청 번호(오래된 응답은 버린다)
    needsReopen: false,  // 토큰 문제 등으로 문제를 다시 열어야 제출할 수 있다
    zoom: 1,
    drag: null,
  };

  // ── 표시 보조 ───────────────────────────────────────────────────────────────
  /** 영상 자리의 안내 문구. retry=true 면 결과 카드와 무관하게 항상 보이는 "다시 시도" 버튼을 함께 보인다. */
  function setStage(text, retry, login) {
    el.stageMessage.textContent = text || "";
    el.stageMessage.hidden = !text;
    el.stageRetry.hidden = !(text && retry);
    if (el.loginForm) el.loginForm.hidden = !(text && login);   // 로그인이 필요할 때만 로그인 칸을 보인다(데모에는 없다)
  }

  /** 로그인 칸을 보인다(실제 연결 화면에만 있다). 영상·박스·결과 안내는 건드리지 않는다. */
  function showLogin() {
    if (!el.loginForm) return;
    el.loginMessage.textContent = "";
    el.loginForm.hidden = false;
    el.loginId.focus();
  }

  function setBasis(kind) {
    el.basisReview.textContent = BASIS_TEXT[kind] || BASIS_TEXT.pending;
  }

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function pct(value) {
    return (value * 100).toFixed(4) + "%";
  }

  function renderBox() {
    const box = state.box;
    if (!box) {
      el.userBox.hidden = true;
      return;
    }
    el.userBox.hidden = false;
    el.userBox.style.left = pct(box.x);
    el.userBox.style.top = pct(box.y);
    el.userBox.style.width = pct(box.width);
    el.userBox.style.height = pct(box.height);
    el.userBox.classList.toggle("is-locked", state.locked);
  }

  function updateControls() {
    const ready = !!state.task;
    const editable = ready && !state.locked && !state.busy && !state.opening;
    el.btnDefault.disabled = !editable;
    el.btnReset.disabled = !editable || !state.box;
    el.zoomIn.disabled = !ready;
    el.zoomOut.disabled = !ready;
    // 박스를 그린 영상이 지금 보이는 영상과 같을 때만 제출할 수 있다(영상이 바뀌면 직접 확인하거나 새로 그려야 한다).
    const boxMatchesAsset = !!state.box && state.boxAsset !== null && state.boxAsset === state.asset;
    el.btnSubmit.disabled = !(editable && !state.needsReopen && boxMatchesAsset && Core.isBoxValid(state.box));
    el.btnSubmit.textContent = state.busy ? "제출 중…" : "제출하고 비교하기";
    el.btnStageRetry.disabled = state.opening;
    el.btnReopen.disabled = state.opening;
    el.btnUseOldBox.disabled = !(ready && state.stash && !state.opening);
    el.btnRedraw.disabled = state.opening;
    el.wrap.classList.toggle("is-locked", state.locked || state.busy);
  }

  function layoutImage() {
    if (!state.task) return;
    const { imageWidth, imageHeight } = state.task;
    const available = Math.max(160, el.stage.clientWidth - 2);
    const maxHeight = Math.max(240, window.innerHeight * 0.68);
    const base = Math.min(available, maxHeight * (imageWidth / imageHeight));
    el.wrap.style.width = Math.round(base * state.zoom) + "px";
    el.wrap.style.aspectRatio = imageWidth + " / " + imageHeight;
    el.zoomLabel.textContent = Math.round(state.zoom * 100) + "%";
  }

  // ── 영상이 바뀌었을 때의 안내 ─────────────────────────────────────────────────
  function hideChangeNotice() {
    el.changeNotice.hidden = true;
    el.changeNoticeText.textContent = "";
  }

  function showChangeNotice(previous, next) {
    el.changeNoticeText.textContent = Core.describeAssetChange(previous, next)
      + " 새 영상에 다시 그리거나, 이전 박스를 이 영상에 쓰겠다고 직접 확인해 주세요.";
    el.changeNotice.hidden = false;
  }

  /** 보관해 둔 이전 박스를 버린다(새로 그리기 또는 새 박스를 그리기 시작할 때). */
  function discardStash() {
    state.stash = null;
    hideChangeNotice();
  }

  // ── 결과 카드 ───────────────────────────────────────────────────────────────
  function hideResult() {
    el.card.hidden = true;
    el.verdict.textContent = "";
    el.verdict.className = "verdict";
    el.iou.hidden = true;
    el.rule.hidden = true;
    el.legend.hidden = true;
    el.message.textContent = "";
    clearChildren(el.notices);
    el.btnRetry.hidden = true;
    el.btnReopen.hidden = true;
    el.refRect.setAttribute("hidden", "");   // SVG 요소에는 hidden 속성(프로퍼티)이 없어 속성으로 직접 다룬다
  }

  function showNotices(list) {
    clearChildren(el.notices);
    (list || []).forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      el.notices.appendChild(li);
    });
  }

  function showCard() {
    el.card.hidden = false;
  }

  function showGraded(view) {
    hideResult();
    el.verdict.textContent = view.label;
    el.verdict.className = "verdict tone-" + view.tone;
    el.iouValue.textContent = view.iouText;
    el.iou.hidden = false;
    if (view.rule && Number.isFinite(view.rule.correct_iou) && Number.isFinite(view.rule.partial_iou)) {
      el.rule.textContent = "기준: IoU " + view.rule.correct_iou + " 이상 일치 · " + view.rule.partial_iou + " 이상 일부 일치";
      el.rule.hidden = false;
    }
    el.message.textContent = "기준 영역은 데이터셋의 ACL 참고 영역입니다. 임상 검증이 아닌 학습용 비교 결과입니다.";
    showNotices(view.notices);
    const ref = view.referenceBox;
    el.refRect.setAttribute("x", ref.x);
    el.refRect.setAttribute("y", ref.y);
    el.refRect.setAttribute("width", ref.width);
    el.refRect.setAttribute("height", ref.height);
    el.refRect.removeAttribute("hidden");
    el.legend.hidden = false;
    el.btnRetry.hidden = false;
    state.locked = true;
    state.needsReopen = false;
    showCard();
    renderBox();
    updateControls();
    el.btnRetry.focus();
  }

  /** 채점하지 않은 응답(graded=false)이나 오류. 정답·오답으로 보이지 않게 사유만 안내하고 박스를 보존한다. */
  function showNotice(message, options) {
    const opts = options || {};
    hideResult();
    el.verdict.textContent = opts.title || "채점하지 않았습니다";
    el.verdict.className = "verdict tone-none";
    el.message.textContent = message;
    showNotices(opts.notices);
    el.btnReopen.hidden = !opts.reopen;           // 문제 다시 열기(대표 영상이 바뀌었는지 확인)
    state.needsReopen = !!opts.blockSubmit;       // 같은 토큰으로는 풀 수 없는 사유만 제출을 막는다
    showCard();
    updateControls();
  }

  function resetAttempt() {
    state.box = null;
    state.boxAsset = null;
    state.locked = false;
    state.needsReopen = false;
    hideResult();
    renderBox();
    updateControls();
  }

  // ── 어댑터: demo(시험용 응답) / live(서버) ──────────────────────────────────────
  function makeSyntheticImage() {
    const w = 400, h = 320;
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g = c.getContext("2d");
    const bg = g.createLinearGradient(0, 0, w, h);
    bg.addColorStop(0, "#10201b"); bg.addColorStop(1, "#050908");
    g.fillStyle = bg; g.fillRect(0, 0, w, h);
    g.strokeStyle = "#3b5a4d"; g.lineWidth = 14;
    g.beginPath(); g.ellipse(200, 150, 120, 90, 0.15, 0, Math.PI * 2); g.stroke();
    g.fillStyle = "#1c2f27"; g.beginPath(); g.ellipse(200, 150, 100, 72, 0.15, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#6b8c7c"; g.fillRect(150, 120, 100, 14);
    g.fillRect(170, 150, 60, 10);
    g.strokeStyle = "#2b3f36"; g.lineWidth = 1;
    for (let i = 40; i < w; i += 40) { g.beginPath(); g.moveTo(i, 0); g.lineTo(i, h); g.stroke(); }
    for (let j = 40; j < h; j += 40) { g.beginPath(); g.moveTo(0, j); g.lineTo(w, j); g.stroke(); }
    g.fillStyle = "#a5b9af"; g.font = "14px sans-serif";
    g.fillText("합성 영상 (시연용 · 실제 환자 영상 아님)", 14, h - 14);
    return { url: c.toDataURL("image/png"), width: w, height: h };
  }

  const demo = {
    async loadTask() {
      const image = makeSyntheticImage();
      return {
        status: 200,
        body: {
          study_idx: 0,
          knee_task: {
            schema_version: "knee-representative-v1", mode: "representative_slice", representative_slice_index: 12,
            display_slice_number: 13, slice_count: 24, image_url: image.url, image_width: image.width,
            image_height: image.height, coord_system: "normalized_image_top_left", grading_available: true,
            unavailable_reason: null, problem_token: "demo-token-not-a-real-token", presentation_id: "0123456789abcdef",
            task_label: "ACL이 포함된 참고 영역 표시 연습",
          },
        },
      };
    },
    async submit(fields) {
      const echo = {
        x: Number(fields.x), y: Number(fields.y), width: Number(fields.width), height: Number(fields.height),
      };
      const note = ["시험용 고정 응답입니다. 실제 서버 채점이 아닙니다."];
      const graded = (result, iou, reference) => ({
        status: 200,
        body: {
          ok: true, graded: true, not_graded_reason: null, not_graded_message: null, result, iou, review_idx: null,
          rule: { metric: "iou", version: "demo", correct_iou: 0.5, partial_iou: 0.2 },
          user_roi_bbox: echo, reference_bbox: reference, body_part: "knee", notices: note,
        },
      });
      const ungraded = (reason, message) => ({
        status: 200,
        body: {
          ok: true, graded: false, not_graded_reason: reason, not_graded_message: message, result: null, iou: null,
          review_idx: null, notices: note,
        },
      });
      switch (el.scenario ? el.scenario.value : "graded_match") {
        case "graded_partial": return graded("부분정답", 0.31204, { x: 0.3, y: 0.3, width: 0.4, height: 0.4 });
        case "graded_differ": return graded("오답", 0.04117, { x: 0.52, y: 0.5, width: 0.3, height: 0.3 });
        case "ungraded_no_reference": return ungraded("no_reference_for_slice", "");
        case "ungraded_token_expired": return ungraded("token_expired", "");
        case "error_outside": return { status: 400, body: { detail: "박스는 이미지 안에 있어야 합니다." } };
        case "error_unauthorized": return { status: 401, body: { detail: "로그인이 필요합니다." } };
        case "error_server": return { status: 503, body: { detail: "이 기능은 지금 사용할 수 없습니다." } };
        case "error_network": throw new TypeError("network");
        default: return graded("정답", 0.62341, { x: 0.3, y: 0.3, width: 0.4, height: 0.4 });
      }
    },
  };

  async function request(url, init) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch(url, Object.assign({ credentials: "same-origin", signal: controller.signal }, init));
      let json = null;
      try { json = await res.json(); } catch (err) { json = null; }
      return { status: res.status, body: json };
    } finally {
      clearTimeout(timer);
    }
  }

  const live = {
    loadTask() {
      return request("/learning/knee/task?study_idx=" + encodeURIComponent(String(STUDY_IDX)), { method: "GET", cache: "no-store" });
    },
    submit(fields, task) {
      const form = new FormData();
      form.append("study_idx", String(STUDY_IDX));
      form.append("roi_type", fields.roi_type);
      form.append("x", fields.x);
      form.append("y", fields.y);
      form.append("width", fields.width);
      form.append("height", fields.height);
      form.append("slice_index", String(task.sliceIndex));
      form.append("problem_token", task.problemToken);
      return request("/learning/roi-grade/knee", { method: "POST", body: form });
    },
  };

  const adapter = MODE === "demo" ? demo : live;

  // ── 문제 열기 ───────────────────────────────────────────────────────────────
  /**
   * 문제를 연다. keepDraft=true(재열기·재시도)이면 박스 초안을 보관한 채로 열고, 같은 영상일 때만 돌려준다.
   * 실패하면 항상 화면에 "다시 시도" 버튼(#btnStageRetry)이 남는다. 열기 진행 중에는 다시 부를 수 없다(중복·경쟁 방지).
   */
  async function openTask(options) {
    const keepDraft = !!(options && options.keepDraft);
    if (state.opening) return;
    state.opening = true;
    const seq = ++state.openSeq;
    const isStale = () => seq !== state.openSeq;

    state.task = null;
    state.asset = null;
    state.locked = false;
    state.needsReopen = false;
    el.wrap.hidden = true;
    el.sliceInfo.textContent = "";
    hideResult();
    setStage("문제를 불러오는 중입니다…", false);
    updateControls();

    const draftNote = () => (state.box || state.stash ? " 작업 중이던 박스는 보관되어 있습니다." : "");
    const fail = (message, loginNeeded) => {
      state.opening = false;
      setBasis(MODE === "demo" ? "demo" : "pending");
      setStage(message + draftNote(), true, !!loginNeeded);
      updateControls();
    };

    if (MODE === "live" && STUDY_IDX === null) {
      state.opening = false;
      setStage("문제 번호가 필요합니다. 주소 끝에 ?study_idx=문제번호 를 붙여 열어 주세요.", false);
      updateControls();
      return;
    }

    let reply;
    try {
      reply = await adapter.loadTask();
    } catch (err) {
      if (!isStale()) fail(Core.describeOpenError(0).message);
      return;
    }
    if (isStale()) return;
    if (reply.status !== 200) {
      fail(Core.describeOpenError(reply.status, reply.body && reply.body.detail).message, reply.status === 401);
      return;
    }
    const task = Core.interpretTask(reply.body, { allowDataUrl: MODE === "demo" });
    if (!task.usable) {
      fail(task.message);
      return;
    }

    let timer = null;
    const finishImage = () => {
      clearTimeout(timer);
      el.img.onload = el.img.onerror = null;
    };
    timer = setTimeout(() => {
      if (isStale() || !state.opening) return;
      finishImage();
      fail("영상을 불러오는 데 너무 오래 걸립니다. 다시 시도해 주세요.");
    }, IMAGE_TIMEOUT_MS);

    el.img.onload = function () {
      if (isStale()) return;
      finishImage();
      if (!Core.imageSizeMatches(el.img.naturalWidth, el.img.naturalHeight, task.imageWidth, task.imageHeight)) {
        fail("영상 크기가 서버 정보와 달라 채점할 수 없습니다. 다시 시도해 주세요.");
        return;
      }
      const asset = Core.assetIdentity(task);
      state.task = task;
      state.asset = asset;
      state.opening = false;

      // 박스 초안: 같은 영상이면 돌려주고, 아니면 자동으로 옮기지 않고 보관만 한다.
      const draft = state.box ? { box: state.box, asset: state.boxAsset } : state.stash;
      if (!keepDraft) {
        state.box = null;
        state.boxAsset = null;
        discardStash();
      } else if (draft) {
        if (Core.sameAsset(draft.asset, asset)) {
          state.box = draft.box;
          state.boxAsset = asset;
          discardStash();
        } else {
          state.box = null;
          state.boxAsset = null;
          state.stash = draft;
          showChangeNotice(draft.asset, asset);
        }
      }

      setStage("", false);
      setBasis(MODE === "demo" ? "demo" : "reviewed");
      el.wrap.hidden = false;
      layoutImage();
      el.sliceInfo.textContent = "대표 영상: " + task.displayNumber + "번째" + (task.sliceCount ? " / " + task.sliceCount + "장" : "")
        + " · 이 한 장에서만 박스를 그립니다.";
      renderBox();
      updateControls();
    };
    el.img.onerror = function () {
      if (isStale()) return;
      finishImage();
      fail("영상을 불러오지 못했습니다. 다시 시도해 주세요.");
    };
    el.img.src = task.imageUrl;
  }

  // ── 로그인 ──────────────────────────────────────────────────────────────────
  /**
   * 학습자 로그인(POST /login). 성공(200)하면 서버가 세션 쿠키를 만들고, 화면은 문제를 다시 연다(박스 초안 보존).
   * 응답 본문은 읽지 않는다(공용 로그인 응답의 개발용 필드가 화면·메모리에 남지 않게). 비밀번호는 요청이 끝나면 곧바로 지우고,
   * 어디에도 저장·기록하지 않는다. 실패 문구는 상태 코드로만 고른다.
   */
  async function submitLogin(event) {
    event.preventDefault();
    if (!el.loginForm || state.loginBusy || state.opening) return;
    const userId = el.loginId.value.trim();
    const password = el.loginPw.value;
    if (!userId || !password) {
      el.loginMessage.textContent = Core.describeLoginError(422);
      return;
    }
    state.loginBusy = true;
    el.btnLogin.disabled = true;
    el.loginMessage.textContent = "";
    let status = 0;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch("/login", {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, user_pw: password }),
      });
      status = res.status;
    } catch (err) {
      status = 0;
    } finally {
      clearTimeout(timer);
      el.loginPw.value = "";
      state.loginBusy = false;
      el.btnLogin.disabled = false;
    }
    if (status === 200) {
      el.loginId.value = "";
      el.loginMessage.textContent = "";
      el.loginForm.hidden = true;
      openTask({ keepDraft: true });
    } else {
      el.loginMessage.textContent = Core.describeLoginError(status);
      el.loginPw.focus();
    }
  }
  if (el.loginForm) el.loginForm.addEventListener("submit", submitLogin);

  // ── 제출 ────────────────────────────────────────────────────────────────────
  async function submit() {
    if (state.busy || state.locked || state.opening || !state.task) return;
    if (state.boxAsset === null || state.boxAsset !== state.asset) return;   // 다른 영상에서 그린 박스는 제출하지 않는다
    const fields = Core.toSubmitFields(state.box);
    if (!fields) {
      showNotice("이미지 안에 충분한 크기의 박스를 그려 주세요.", { title: "제출할 수 없습니다" });
      return;
    }
    state.busy = true;
    updateControls();
    let reply = null;
    try {
      reply = await adapter.submit(fields, state.task);
    } catch (err) {
      reply = null;   // 통신 실패·시간 초과. 서버가 이미 처리했는지는 알 수 없다. 박스는 그대로 두고 다시 제출할 수 있게 한다.
    } finally {
      state.busy = false;
    }
    if (!reply) {
      showNotice(Core.describeHttpError(0).message, { title: "제출 결과를 받지 못했습니다" });
      return;
    }
    if (reply.status !== 200) {
      showNotice(Core.describeHttpError(reply.status, reply.body && reply.body.detail).message, { title: "제출하지 못했습니다" });
      // 세션이 끊겼으면(401) 영상과 박스는 그대로 두고 **같은 페이지에서** 로그인할 수 있게 한다. 성공하면 문제를 keepDraft 로 다시 열어
      // 같은 영상이면 박스를 돌려주고, 대표 영상이 바뀌었으면 변경 안내·직접 확인 전에는 제출할 수 없다(새로고침이 복구 전제가 아니다).
      if (reply.status === 401) showLogin();
      return;
    }
    const view = Core.interpretGradeResponse(reply.body);
    if (view.kind === "graded") {
      showGraded(view);
    } else {
      showNotice(view.message, { notices: view.notices, reopen: !!view.canReopen, blockSubmit: !!view.needsReopen });
    }
  }

  // ── 박스 그리기·이동·크기 조절 ──────────────────────────────────────────────────
  function pointerNormalized(event) {
    return Core.pointToNormalized(event.clientX, event.clientY, el.wrap.getBoundingClientRect());
  }

  function onPointerDown(event) {
    if (!state.task || state.locked || state.busy || state.opening || (event.button !== undefined && event.button !== 0)) return;
    const p = pointerNormalized(event);
    if (!p) return;
    const handle = event.target.closest && event.target.closest(".handle");
    if (handle && state.box) {
      state.drag = { type: "resize", handle: handle.dataset.handle };
    } else if (event.target.closest && event.target.closest("#userBox") && state.box) {
      state.drag = { type: "move", start: p, origin: Object.assign({}, state.box) };
    } else {
      state.drag = { type: "draw", start: p, previous: state.box, previousAsset: state.boxAsset };
      if (state.stash) discardStash();   // 새로 그리기 시작하면 보관해 둔 이전 박스는 버린다
    }
    state.boxAsset = state.asset;        // 이 박스는 지금 보이는 영상 위에서 만든다
    el.wrap.setPointerCapture && el.wrap.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function onPointerMove(event) {
    const drag = state.drag;
    if (!drag) return;
    const p = pointerNormalized(event);
    if (!p) return;
    if (drag.type === "draw") {
      state.box = Core.boxFromPoints(drag.start, p);
    } else if (drag.type === "move") {
      state.box = Core.moveBox(drag.origin, p.x - drag.start.x, p.y - drag.start.y);
    } else if (drag.type === "resize" && state.box) {
      state.box = Core.resizeBox(state.box, drag.handle, p);
    }
    renderBox();
    updateControls();
  }

  function onPointerUp(event) {
    const drag = state.drag;
    if (!drag) return;
    state.drag = null;
    el.wrap.releasePointerCapture && el.wrap.hasPointerCapture && el.wrap.hasPointerCapture(event.pointerId)
      && el.wrap.releasePointerCapture(event.pointerId);
    if (drag.type === "draw" && !Core.isBoxValid(state.box)) {
      state.box = drag.previous || null;   // 클릭만 했거나 너무 작으면 이전 박스를 그대로 둔다
      state.boxAsset = drag.previous ? drag.previousAsset : null;
    }
    renderBox();
    updateControls();
  }

  el.wrap.addEventListener("pointerdown", onPointerDown);
  el.wrap.addEventListener("pointermove", onPointerMove);
  el.wrap.addEventListener("pointerup", onPointerUp);
  el.wrap.addEventListener("pointercancel", onPointerUp);
  el.wrap.addEventListener("dragstart", (event) => event.preventDefault());

  el.userBox.addEventListener("keydown", (event) => {
    if (state.locked || state.busy || state.opening || !state.box) return;
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    state.box = Core.nudgeBox(state.box, event.key, event.shiftKey);
    state.boxAsset = state.asset;
    renderBox();
    updateControls();
  });

  // ── 버튼 ────────────────────────────────────────────────────────────────────
  el.btnDefault.addEventListener("click", () => {
    if (!state.task || state.locked || state.busy || state.opening) return;
    discardStash();
    state.box = Core.defaultBox();
    state.boxAsset = state.asset;
    renderBox();
    updateControls();
    el.userBox.focus({ preventScroll: true });
  });
  el.btnReset.addEventListener("click", () => {
    if (state.locked || state.busy || state.opening) return;
    state.box = null;
    state.boxAsset = null;
    state.needsReopen = false;
    hideResult();
    renderBox();
    updateControls();
  });
  el.btnSubmit.addEventListener("click", submit);
  el.btnRetry.addEventListener("click", resetAttempt);
  el.btnReopen.addEventListener("click", () => openTask({ keepDraft: true }));
  el.btnStageRetry.addEventListener("click", () => openTask({ keepDraft: true }));
  el.btnRedraw.addEventListener("click", () => {
    discardStash();
    state.box = null;
    state.boxAsset = null;
    renderBox();
    updateControls();
  });
  el.btnUseOldBox.addEventListener("click", () => {
    // 명시적 확인: 이전 영상에서 그린 박스를 지금 영상에 그대로 쓰겠다고 사용자가 직접 선택했다.
    if (!state.task || !state.stash || state.opening) return;
    state.box = state.stash.box;
    state.boxAsset = state.asset;
    discardStash();
    renderBox();
    updateControls();
    el.userBox.focus({ preventScroll: true });
  });
  el.zoomIn.addEventListener("click", () => {
    const i = ZOOM_STEPS.indexOf(state.zoom);
    state.zoom = ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, i + 1)];
    layoutImage();
  });
  el.zoomOut.addEventListener("click", () => {
    const i = ZOOM_STEPS.indexOf(state.zoom);
    state.zoom = ZOOM_STEPS[Math.max(0, i - 1)];
    layoutImage();
  });
  window.addEventListener("resize", layoutImage);

  openTask({ keepDraft: false });
})();
