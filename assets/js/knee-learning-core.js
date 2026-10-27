/*
 * 무릎 ROI 학습 화면의 "계산 없는" 핵심 규칙 모음 (화면 없이 시험할 수 있게 따로 뺐다).
 *
 * 여기에는 박스 좌표 변환·편집, 제출값 만들기, 서버 응답을 화면 문구로 바꾸는 일만 있다.
 * 채점(IoU 계산, 기준 박스, 정답 판정)은 하지 않는다. 그것은 서버(POST /learning/roi-grade/knee)만 한다.
 *
 * 좌표 규칙: 이미지 전체를 0~1 로 본 왼쪽 위 기준 값(normalized). 화면 크기·확대·여백과 무관하다.
 * 브라우저와 Node 양쪽에서 불러올 수 있다(window.KneeLearningCore / require).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.KneeLearningCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const MIN_SIZE = 0.02;          // 박스 한 변의 최소 길이(이미지 대비). 너무 작은 박스는 만들지 않는다.
  const KEY_STEP = 0.005;         // 방향키 한 번에 움직이는 양(이미지 대비)
  const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /** 화면 좌표(clientX/Y)를 이미지 전체 기준 0~1 좌표로 바꾼다. rect 는 "실제 영상이 그려진 상자"의 위치·크기여야 한다. */
  function pointToNormalized(clientX, clientY, rect) {
    if (!rect || !(rect.width > 0) || !(rect.height > 0)) return null;
    return {
      x: clamp((clientX - rect.left) / rect.width, 0, 1),
      y: clamp((clientY - rect.top) / rect.height, 0, 1),
    };
  }

  /** 두 점으로 박스를 만든다(드래그 시작점과 현재점). 방향과 무관하게 왼쪽 위 기준으로 정리한다. */
  function boxFromPoints(a, b) {
    const x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x);
    const y1 = Math.min(a.y, b.y), y2 = Math.max(a.y, b.y);
    return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
  }

  /** 박스를 (dx, dy) 만큼 옮기되 이미지 밖으로 나가지 않게 한다. 크기는 그대로. */
  function moveBox(box, dx, dy) {
    return {
      x: clamp(box.x + dx, 0, 1 - box.width),
      y: clamp(box.y + dy, 0, 1 - box.height),
      width: box.width,
      height: box.height,
    };
  }

  /**
   * 손잡이(handle)를 점 p(0~1)로 끌었을 때의 박스. 반대쪽 변은 고정하고 이미지 안·최소 크기를 지킨다.
   * handle: nw n ne e se s sw w
   */
  function resizeBox(box, handle, p, minSize) {
    const min = typeof minSize === "number" ? minSize : MIN_SIZE;
    let left = box.x, top = box.y, right = box.x + box.width, bottom = box.y + box.height;
    const px = clamp(p.x, 0, 1), py = clamp(p.y, 0, 1);
    if (handle.indexOf("w") >= 0) left = Math.min(px, right - min);
    if (handle.indexOf("e") >= 0) right = Math.max(px, left + min);
    if (handle.indexOf("n") >= 0) top = Math.min(py, bottom - min);
    if (handle.indexOf("s") >= 0) bottom = Math.max(py, top + min);
    return { x: left, y: top, width: right - left, height: bottom - top };
  }

  /** 이미지 안에 있고 크기가 충분한 박스인가. (NaN·Infinity·음수·밖으로 나간 값은 모두 거절) */
  function isBoxValid(box, minSize) {
    if (!box) return false;
    const min = typeof minSize === "number" ? minSize : MIN_SIZE;
    const values = [box.x, box.y, box.width, box.height];
    if (!values.every((v) => typeof v === "number" && Number.isFinite(v))) return false;
    if (box.width < min || box.height < min || box.x < 0 || box.y < 0) return false;
    return box.x + box.width <= 1 + 1e-9 && box.y + box.height <= 1 + 1e-9;
  }

  /** 중앙에 기본 크기(한 변 30%) 박스를 만든다. 마우스를 못 쓰는 사용자를 위한 시작점. */
  function defaultBox() {
    return { x: 0.35, y: 0.35, width: 0.3, height: 0.3 };
  }

  /** 방향키로 이동/크기 조절. shift=크기(오른쪽·아래 변), 그 밖에는 이동. 이미지 안에서만. */
  function nudgeBox(box, key, shift) {
    const dx = key === "ArrowLeft" ? -KEY_STEP : key === "ArrowRight" ? KEY_STEP : 0;
    const dy = key === "ArrowUp" ? -KEY_STEP : key === "ArrowDown" ? KEY_STEP : 0;
    if (!dx && !dy) return box;
    if (!shift) return moveBox(box, dx, dy);
    return resizeBox(box, "se", { x: box.x + box.width + dx, y: box.y + box.height + dy });
  }

  function round6(value) {
    return Math.round(value * 1e6) / 1e6;
  }

  /**
   * 서버에 보낼 값. 화면에 보이는 박스와 같은 좌표를 6자리 문자열로 만든다(반올림 때문에 이미지 밖으로 나가지 않게
   * 오른쪽·아래 끝을 1 이하로 맞춘다). 거절할 박스면 null.
   */
  function toSubmitFields(box) {
    if (!isBoxValid(box)) return null;
    const x1 = round6(box.x), y1 = round6(box.y);
    const x2 = Math.min(1, round6(box.x + box.width)), y2 = Math.min(1, round6(box.y + box.height));
    const out = { x: x1, y: y1, width: round6(x2 - x1), height: round6(y2 - y1) };
    if (!(out.width > 0 && out.height > 0)) return null;
    return {
      roi_type: "box",
      x: out.x.toFixed(6), y: out.y.toFixed(6), width: out.width.toFixed(6), height: out.height.toFixed(6),
    };
  }

  // ── 서버 응답 → 화면 표시용 값 ─────────────────────────────────────────────────
  // 설계 5장: 결과 표현은 "기준 영역과 일치 / 일부 일치 / 차이 있음". 진단 정확도·정상·파열 범위 같은 말은 쓰지 않는다.
  const RESULT_LABELS = {
    "정답": { label: "기준 영역과 일치", tone: "match" },
    "부분정답": { label: "일부 일치", tone: "partial" },
    "오답": { label: "차이 있음", tone: "differ" },
  };

  const NOT_GRADED_GUIDE = {
    reference_not_approved: "이 문제는 승인된 기준 영역이 아직 준비되지 않아 채점하지 않습니다.",
    slide_not_supported: "이 문제는 여러 장(슬라이드)으로 되어 있어 아직 채점하지 않습니다.",
    study_image_unavailable: "이 문제의 이미지를 확인할 수 없어 채점하지 않습니다.",
    no_reference_for_slice: "이 슬라이스에는 승인된 기준 영역이 없어 채점하지 않습니다. 대표 영상을 확인해 주세요.",
    reference_ambiguous: "이 문제의 기준 영역 정보를 확인할 수 없어 채점하지 않습니다.",
    token_invalid: "문제 화면 정보를 확인할 수 없어 채점하지 않습니다. 문제를 다시 열어 주세요.",
    token_expired: "문제 화면의 유효 시간이 지났습니다. 문제를 다시 열어 주세요.",
    presentation_changed: "문제 내용이 바뀌어 채점하지 않습니다. 문제를 다시 열어 주세요.",
    asset_changed: "이 문제의 영상을 확인할 수 없어 채점하지 않습니다.",
  };
  const NOT_GRADED_FALLBACK = "이 제출은 채점하지 않았습니다.";
  // 문제를 다시 열어야 풀 수 있는 사유(박스를 보존해도 같은 토큰으로는 해결되지 않는다)
  const REOPEN_REASONS = ["token_invalid", "token_expired", "presentation_changed"];

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function validNormalizedBox(box) {
    return isPlainObject(box) && isBoxValid(box, 0);
  }

  /**
   * POST /learning/roi-grade/knee 의 성공(200) 응답을 화면 표시용으로 바꾼다.
   * - graded === true 이고 판정 문구가 알려진 값일 때만 "채점됨".
   * - graded 가 true 가 아니면 정답/오답으로 보이지 않게 사유만 준다. review_idx·점수는 쓰지 않는다.
   * - 모양이 이상한 응답은 채점됨으로 취급하지 않는다(알 수 없는 응답 = 미채점 안내).
   */
  function interpretGradeResponse(body) {
    if (!isPlainObject(body)) return { kind: "unknown", message: NOT_GRADED_FALLBACK, canRetry: true };
    if (body.graded === true) {
      const verdict = RESULT_LABELS[body.result];
      const iou = typeof body.iou === "number" && Number.isFinite(body.iou) ? body.iou : null;
      if (verdict && iou !== null && validNormalizedBox(body.reference_bbox)) {
        return {
          kind: "graded",
          label: verdict.label,
          tone: verdict.tone,
          iou,
          iouText: iou.toFixed(5),
          referenceBox: body.reference_bbox,
          userBox: validNormalizedBox(body.user_roi_bbox) ? body.user_roi_bbox : null,
          rule: isPlainObject(body.rule) ? body.rule : null,
          sliceIndex: Number.isInteger(body.slice_index) ? body.slice_index : null,
          notices: Array.isArray(body.notices) ? body.notices.filter((t) => typeof t === "string") : [],
        };
      }
      return { kind: "unknown", message: NOT_GRADED_FALLBACK, canRetry: true };
    }
    const reason = typeof body.not_graded_reason === "string" ? body.not_graded_reason : "";
    const message = NOT_GRADED_GUIDE[reason]
      || (typeof body.not_graded_message === "string" && body.not_graded_message ? body.not_graded_message : NOT_GRADED_FALLBACK);
    return {
      kind: "ungraded",
      reason,
      message,
      needsReopen: REOPEN_REASONS.indexOf(reason) >= 0,   // 같은 토큰으로는 해결되지 않아 제출을 막는다
      canReopen: true,                                    // 대표 영상이 바뀐 경우(no_reference_for_slice 등)도 다시 열어 확인할 수 있다
      notices: Array.isArray(body.notices) ? body.notices.filter((t) => typeof t === "string") : [],
    };
  }

  /** HTTP 오류 상태를 화면 문구로. detail 은 서버가 준 문자열일 때만 덧붙인다. */
  function describeHttpError(status, detail) {
    const extra = typeof detail === "string" && detail ? detail : "";
    switch (status) {
      case 400: return { message: "박스 또는 요청 값을 확인해 주세요." + (extra ? " (" + extra + ")" : ""), canRetry: true };
      case 401: return { message: "로그인이 필요합니다. 로그인한 뒤 다시 제출해 주세요. 박스는 그대로 남아 있습니다.", canRetry: true };
      case 403: return { message: "이메일 인증을 마친 뒤 사용할 수 있습니다.", canRetry: false };
      case 404: return { message: "채점할 수 있는 문제가 아닙니다.", canRetry: false };
      case 409: return { message: "문제 내용이 바뀌었습니다. 다시 시도해서 새로 열어 주세요.", canRetry: true };
      case 422: return { message: "요청 형식이 올바르지 않습니다. 화면을 새로 열어 주세요.", canRetry: false };
      case 502: return { message: "결과를 저장하지 못했습니다. 잠시 뒤 다시 제출해 주세요. 박스는 그대로 남아 있습니다.", canRetry: true };
      case 503: return { message: "이 기능은 지금 사용할 수 없습니다.", canRetry: false };
      default: return { message: "서버와 통신하지 못했습니다. 응답을 받지 못했다면 제출이 이미 처리됐을 수도 있습니다. 박스는 그대로 남아 있어 다시 제출할 수 있지만, 이미 처리됐다면 기록이 한 번 더 남을 수 있습니다.", canRetry: true };
    }
  }

  /**
   * 로그인 요청(POST /login) 실패 문구. 서버 응답 본문(detail)은 쓰지 않는다 — 공용 로그인 응답에 개발용 필드가 섞여 있어
   * 그대로 보여 주면 입력한 비밀번호가 화면에 나올 수 있다. 상태 코드만 보고 고정 문구를 쓴다.
   */
  function describeLoginError(status) {
    if (status === 401) return "아이디 또는 비밀번호가 올바르지 않습니다.";
    if (status === 400 || status === 422) return "아이디와 비밀번호를 모두 입력해 주세요.";
    if (status === 429) return "시도가 너무 많습니다. 잠시 뒤 다시 시도해 주세요.";
    if (status === 0 || status === undefined || status === null) return "서버와 통신하지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.";
    return "로그인하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
  }

  /** 문제를 여는 요청(GET /learning/knee/task)이 실패했을 때의 문구. 제출 실패 문구와 달리 "다시 시도"로 이어지게 쓴다. */
  function describeOpenError(status, detail) {
    if (status === 401) return { message: "로그인이 필요합니다. 로그인한 뒤 '다시 시도'를 눌러 주세요.", canRetry: true };
    if (status === 403) return { message: "이메일 인증을 마친 뒤 사용할 수 있습니다. 인증한 뒤 '다시 시도'를 눌러 주세요.", canRetry: true };
    if (status === 0 || status === undefined) return { message: "서버와 통신하지 못했습니다. 연결을 확인한 뒤 '다시 시도'를 눌러 주세요.", canRetry: true };
    const info = describeHttpError(status, detail);
    return { message: info.message, canRetry: true };   // 열기 실패는 어떤 경우에도 다시 시도할 수 있게 한다
  }

  /**
   * GET /learning/knee/task 응답(knee_task)을 화면에 쓸 수 있는지 검사한다.
   * 쓸 수 없으면 {usable:false, message}. 이미지 주소는 같은 Origin 의 상대 경로나 https 주소만 허용한다.
   */
  function interpretTask(body, options) {
    const allowDataUrl = !!(options && options.allowDataUrl);   // 시연(demo)의 합성 영상만 data: 주소를 쓴다
    const task = isPlainObject(body) ? body.knee_task : null;
    if (!isPlainObject(task)) return { usable: false, message: "문제 정보를 읽을 수 없습니다." };
    if (task.grading_available !== true) {
      const reason = typeof task.unavailable_reason === "string" ? task.unavailable_reason : "";
      return { usable: false, message: NOT_GRADED_GUIDE[reason] || "이 문제는 지금 채점할 수 없습니다.", reason };
    }
    const dims = [task.image_width, task.image_height];
    const sliceOk = Number.isInteger(task.representative_slice_index) && task.representative_slice_index >= 0;
    const token = typeof task.problem_token === "string" && task.problem_token ? task.problem_token : null;
    const url = typeof task.image_url === "string" ? task.image_url : "";
    const urlOk = /^\/(?!\/)/.test(url) || /^https:\/\//i.test(url) || (allowDataUrl && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(url));
    if (!sliceOk || !token || !urlOk || !dims.every((v) => Number.isInteger(v) && v > 0)) {
      return { usable: false, message: "문제 정보를 읽을 수 없습니다." };
    }
    const presentationId = typeof task.presentation_id === "string" && /^[0-9a-f]{16}$/.test(task.presentation_id) ? task.presentation_id : null;
    return {
      usable: true,
      presentationId,
      sliceIndex: task.representative_slice_index,
      displayNumber: Number.isInteger(task.display_slice_number) ? task.display_slice_number : task.representative_slice_index + 1,
      sliceCount: Number.isInteger(task.slice_count) ? task.slice_count : null,
      imageUrl: url,
      imageWidth: task.image_width,
      imageHeight: task.image_height,
      problemToken: token,
      label: typeof task.task_label === "string" ? task.task_label : "",
    };
  }

  /** 박스를 그린 "제시 영상"의 동일성 값. 토큰은 발급마다 달라 쓰지 않고, 서버가 준 불투명 식별값·대표 번호·크기만 쓴다. */
  function assetIdentity(task) {
    if (!task || !task.usable) return null;
    return {
      presentationId: task.presentationId || null,
      sliceIndex: task.sliceIndex,
      displayNumber: task.displayNumber,
      imageWidth: task.imageWidth,
      imageHeight: task.imageHeight,
    };
  }

  /**
   * 두 제시가 "같은 영상"인가. 서버 식별값이 양쪽에 있고 같으며 대표 번호·크기도 같을 때만 true.
   * 확인할 수 없으면(식별값 없음 등) false 로 보수적으로 본다 — 이전 박스를 자동으로 새 영상에 올리지 않는다.
   */
  function sameAsset(a, b) {
    if (!a || !b || !a.presentationId || !b.presentationId) return false;
    return a.presentationId === b.presentationId && a.sliceIndex === b.sliceIndex
      && a.imageWidth === b.imageWidth && a.imageHeight === b.imageHeight;
  }

  /** 영상이 바뀌었음을 알리는 문구(내부 경로·해시 없이 화면에 보이는 번호만 쓴다). */
  function describeAssetChange(prev, next) {
    if (prev && next && Number.isInteger(prev.displayNumber) && Number.isInteger(next.displayNumber) && prev.displayNumber !== next.displayNumber) {
      return "대표 영상이 바뀌었습니다(" + prev.displayNumber + "번째 → " + next.displayNumber + "번째). 이전 박스는 이전 영상 기준이라 자동으로 옮기지 않았습니다.";
    }
    return "대표 영상이 바뀌었거나 같은 영상인지 확인할 수 없습니다. 이전 박스는 자동으로 옮기지 않았습니다.";
  }

  /** 실제로 불러온 영상의 크기가 서버가 알려 준 크기와 같은가. 다르면 좌표가 어긋날 수 있어 채점하지 않는다. */
  function imageSizeMatches(naturalWidth, naturalHeight, declaredWidth, declaredHeight) {
    return naturalWidth === declaredWidth && naturalHeight === declaredHeight;
  }

  return {
    MIN_SIZE, KEY_STEP, HANDLES, RESULT_LABELS, NOT_GRADED_GUIDE,
    clamp, pointToNormalized, boxFromPoints, moveBox, resizeBox, isBoxValid, defaultBox, nudgeBox, toSubmitFields,
    interpretGradeResponse, describeHttpError, describeOpenError, describeLoginError, interpretTask, imageSizeMatches, assetIdentity, sameAsset, describeAssetChange,
  };
});
