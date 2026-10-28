/*
 * 화면 분석 결과 공통 표시 모델 어댑터 (MSN-20261021-001, screen_analyze.html 에서 <script src> 로 읽음)
 *
 * 뇌 API(/api/v1/analyze/screen-roi) 응답과 무릎 API(/api/v1/knee/screen-roi) 응답을 **바꾸지 않고**,
 * 화면 한 곳(renderResult)이 그리는 공통 표시 모델로만 옮긴다.
 *   { part, title, status, meta:[{label,value}], groups:[{key,title,findings:[{key,label,score|null,status,reason|null,note|null}]}],
 *     overlay:{src|null,note|null}, explain:{mode:'llm'|'fixed'|'none', items:[{title,text}], disabledReason|null}, notices:[] }
 * 규칙(설계 3장·AC03)
 * - 점수는 0~1 유한한 수만 막대로 쓴다. null·NaN·문자열·범위 밖은 score=null(“점수 없음”)이며 0% 로 바꾸지 않는다.
 * - 무릎 not_evaluated 소견은 score=null + 이유. 정상/0% 로 표시하지 않는다.
 * - 무릎 ACL 이진 점수에서 부분/완전 확률을 만들지 않는다(소견 목록은 서버가 준 것만).
 * - 소견들을 하나의 softmax 로 합치거나 다시 정규화하지 않는다.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ScreenResultAdapter = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function score01(v) {
    return typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1 ? v : null;
  }

  function text(v, fallback) {
    return typeof v === "string" && v.trim() ? v : fallback == null ? null : fallback;
  }

  /** 뇌: classification(출혈)·germinoma 를 각각 소견 그룹으로. 기존 renderAnalysis 와 같은 제목·숫자·히트맵. */
  function fromBrain(out) {
    out = out || {};
    const top = out.classification || {};
    const g = out.germinoma || null;
    let title = `출혈 최상위: ${text(top.top_label_ko, "-")}`;
    if (g && score01(g.confidence) != null) title += ` · germinoma: ${text(g.top_label_ko, "-")} ${(g.confidence * 100).toFixed(0)}%`;
    const toFindings = (list) =>
      (Array.isArray(list) ? list : []).map((f, i) => ({
        key: text(f && f.label, `f${i}`), label: text(f && f.label_ko, "-"), score: score01(f && f.score),
        status: score01(f && f.score) == null ? "invalid_score" : "evaluated", reason: null, note: null,
      }));
    const groups = [{ key: "ich", title: "출혈 (ICH)", findings: toFindings(top.findings) }];
    if (g && g.findings) groups.push({ key: "germinoma", title: "Germinoma", findings: toFindings(g.findings) });
    const meta = [];
    if (out.roi) meta.push({ label: "ROI 크기", value: `${out.roi.width}×${out.roi.height}` });
    if (out.image_stats) meta.push({ label: "밝기 평균/편차", value: `${out.image_stats.mean} / ${out.image_stats.std}` });
    if (out.latency_ms != null) meta.push({ label: "추론 시간", value: `${out.latency_ms}ms` });
    if (out.overlay_summary) meta.push({ label: "이상 부위", value: out.overlay_summary });
    return {
      part: "brain", title, status: "evaluated", meta, groups,
      overlay: { src: out.overlay_png_base64 ? "data:image/png;base64," + out.overlay_png_base64 : null, note: null },
      explain: { mode: "llm", items: [], disabledReason: null },
      notices: [],
    };
  }

  const KNEE_TITLES = {
    evaluated: "무릎 MRI 학습용 분석 결과",
    partially_evaluated: "무릎 MRI 학습용 분석 결과 (일부만 평가)",
    not_evaluated: "평가하지 않음 — 선택한 영상 조건은 분석 성능 확인 중입니다",
  };

  /** 무릎: findings 의 status 를 먼저 보고 evaluated + 유한한 0~1 점수만 막대. 나머지는 점수 없음 + 이유. */
  function fromKnee(out) {
    out = out || {};
    const findings = [];
    const src = out.findings && typeof out.findings === "object" ? out.findings : {};
    for (const key of Object.keys(src)) {
      const f = src[key] || {};
      const s = f.status === "evaluated" ? score01(f.prob) : null;
      const evaluated = f.status === "evaluated" && s != null;
      const band = f.confidence_band === "uncertain" ? "판단 어려움 구간" : null;
      findings.push({
        key, label: text(f.label_ko, key), score: evaluated ? s : null,
        status: evaluated ? (f.confidence_band === "uncertain" ? "uncertain" : "evaluated") : (f.status === "evaluated" ? "invalid_score" : "not_evaluated"),
        reason: evaluated ? null : text(f.reason, "이 조건에서는 평가하지 않습니다."),
        note: evaluated ? [text(f.score_label, "모델 점수(임상 확률 아님)"), band, text(f.verdict, null)].filter(Boolean).join(" · ") : null,
      });
    }
    const vis = out.visualization || {};
    const img = out.image || {};
    const meta = [];
    if (img.width) meta.push({ label: "이미지 크기", value: `${img.width}×${img.height}` });
    if (img.plane) meta.push({ label: "촬영면 / 범위", value: `${img.plane} / ${img.image_scope}` });
    if (out.latency_ms != null) meta.push({ label: "추론 시간", value: `${out.latency_ms}ms` });
    const items = (Array.isArray(out.explanations) ? out.explanations : [])
      .filter((e) => e && text(e.text, null))
      .map((e) => ({ title: text(e.finding, "설명"), text: e.text }));
    const notices = [];
    if (vis.acl_box_note) notices.push(vis.acl_box_note);
    for (const n of Array.isArray(out.notices) ? out.notices : []) if (n && text(n.text, null)) notices.push(n.text);
    if (out.retention && out.retention.scope_note) notices.push(out.retention.scope_note);
    const status = KNEE_TITLES[out.analysis_status] ? out.analysis_status : "not_evaluated";
    return {
      part: "knee", title: KNEE_TITLES[status], status, meta,
      groups: [{ key: "knee", title: "무릎 MRI 소견(데이터셋 라벨 기준 모델 점수)", findings }],
      overlay: { src: vis.overlay_png_base64 ? "data:image/png;base64," + vis.overlay_png_base64 : null, note: text(vis.overlay_note, null) },
      explain: items.length
        ? { mode: "fixed", items, disabledReason: null }
        : { mode: "none", items: [], disabledReason: "검수된 무릎 설명이 있는 결과에서만 해설을 볼 수 있습니다." },
      notices,
    };
  }

  /** 무릎 결과 그림(CAM_LOCALIZATION_STEP2 5장). visualization.kind 로만 고른다.
   *  - 넘기며(volume): kind='anatomical_box' 일 때만 'ACL 대략적 위치 안내'(별도 위치 모델 박스). 여러 장 모델 열지도는 보이지 않는다.
   *    kind 가 없는 이전 형식 응답(볼륨 CAM PNG)·알 수 없는 kind 는 그림을 숨긴다(새 박스로 오인 표시 금지).
   *  - 한 장(single_2d): '모델 참고 열지도(Grad-CAM)'. 이전 형식(kind 없음)도 한 장은 같은 CAM 이므로 같은 이름으로 보인다.
   *  반환 alt 는 템플릿이 img alt 로 쓴다(뇌는 alt 가 없어 기존 기본값 유지). */
  function kneeOverlay(single, vis, cap) {
    const png = vis.status === "shown" && vis.overlay_png_base64 ? "data:image/png;base64," + vis.overlay_png_base64 : null;
    const kind = vis.kind;
    if (single) {
      if (kind !== undefined && kind !== "grad_cam" && kind !== "none") return { src: null, note: null, alt: null };
      const title = text(vis.title, "모델 참고 열지도(Grad-CAM)");
      if (png) return { src: png, alt: title, note: `${title} · ${text(vis.note, "모델 점수에 대한 참고 반응이며 병변 위치·윤곽이 아닙니다.")}` };
      return { src: null, alt: null, note: vis.status === "no_activation" ? "참고 열지도로 표시할 뚜렷한 반응이 없었습니다(점수는 계산됨). 색칠 없음이 정상이라는 뜻은 아닙니다." : null };
    }
    const loc = vis.localization && typeof vis.localization === "object" ? vis.localization : null;
    if (kind === "anatomical_box" && png && loc && loc.status === "shown") {
      const used = cap && cap.used_count != null ? `분석에 쓴 ${cap.used_count}장 중 ${loc.frame_ordinal}번째 장(수집 순번 ${loc.frame_sequence})` : null;
      const lines = [text(vis.title, "ACL 대략적 위치 안내"), text(vis.legend, "ACL이 포함될 수 있는 참고 영역 — 병변 위치 아님"), used,
        text(vis.relation, "점수는 여러 장 분류 모델, 박스는 별도 ACL 위치 모델의 결과입니다. 박스가 점수의 원인이나 손상 위치를 뜻하지 않습니다.")].filter(Boolean);
      return { src: png, alt: "ACL 대략적 위치 안내", note: lines.join(" · ") };
    }
    if (kind === "none" && vis.status === "withheld") return { src: null, alt: null, note: text(vis.note, "위치 안내를 준비하지 못했습니다. 분석 점수는 표시합니다.") };
    if (kind === "none") return { src: null, alt: null, note: null };
    // kind 없음(이전 형식 볼륨 CAM)·알 수 없는 kind: 그림 숨김
    return { src: null, alt: null, note: png ? "이 결과의 위치 그림은 표시하지 않습니다(이전 형식 응답)." : null };
  }

  /** 무릎 화면 분석(POST /api/v1/knee/screen-analysis, schema_version 1): 실제 ACL 점수(보정 전이면 raw)·위치 안내/참고 열지도·템플릿 해설.
   *  model_kind: 'single_2d' = 한 장 모델(선택 영역 분석), 'volume' = 여러 장 모델(넘기며 분석). 제목·모델·소견 그룹 문구만 다르다. */
  function fromKneeScreen(out) {
    out = out || {};
    const single = out.model_kind === "single_2d";
    const acl = out.acl || {};
    const cap = out.capture || {};
    const vis = out.visualization || {};
    const s = score01(acl.display_score);
    const findings = [{
      key: "acl", label: text(acl.label_ko, "ACL 손상 관련 모델 점수"), score: s,
      status: s == null ? "invalid_score" : (acl.decision === "uncertain" ? "uncertain" : "evaluated"),
      reason: s == null ? "점수를 표시할 수 없습니다." : null,
      // 진단 아님 안내는 보정 여부와 무관하게 항상(CALIBRATION_PRIORITY_DESIGN UI01). 비교선·구간은 서버가 고른 값만 옮기고 여기서 계산하지 않는다.
      note: s == null ? null : [text(acl.display_text, null), acl.score_kind === "raw" ? "구분 기준 보정 전" : null, "정상/파열 판정 아님 · 의료 진단 아님"].filter(Boolean).join(" · "),
    }];
    const ref = acl.reference && typeof acl.reference === "object" ? acl.reference : null;
    const meta = [];
    if (cap.used_count != null) meta.push({ label: "분석에 쓴 장", value: cap.received_count !== cap.used_count ? `${cap.used_count}장 (받은 ${cap.received_count}장 중 같은 화면 제외)` : `${cap.used_count}장` });
    if (cap.plane) meta.push({ label: "촬영면 / 범위", value: `${cap.plane} / ${cap.scope}` });
    if (ref && score01(ref.threshold) != null) {
      const pos = ref.position === "above" ? "위" : ref.position === "below" ? "아래" : null;
      meta.push({ label: text(ref.label, "참고선"), value: `${(ref.threshold * 100).toFixed(1)}${pos ? ` · 점수가 참고선보다 ${pos}` : ""}${ref.band_text ? ` · ${ref.band_text}` : ""}` });
    }
    if (out.model_kind) meta.push({ label: "모델", value: single ? "ACL 시상면 한 장 모델" : out.model_kind === "volume" ? "ACL 시상면 여러 장 모델" : "알 수 없는 모델" });
    if (out.latency_ms != null) meta.push({ label: "추론 시간", value: `${out.latency_ms}ms${out.device ? " · " + out.device : ""}` });
    const items = (Array.isArray(out.explanations) ? out.explanations : []).filter((e) => e && text(e.text, null)).map((e) => ({ title: text(e.title, "해설"), text: e.text }));
    const notices = [];
    if (ref && text(ref.text, null)) notices.push(ref.text);
    if (ref && ref.input_condition && /assumed/.test(ref.input_condition)) notices.push("참고선은 데이터셋 조건(가운데 장/가운데 15장)이라고 가정한 비교입니다. 서버는 화면 입력이 그 조건인지 확인하지 못했습니다.");
    for (const w of Array.isArray(out.warnings) ? out.warnings : []) if (w && text(w.text, null)) notices.push(w.text);
    for (const n of Array.isArray(out.notices) ? out.notices : []) if (n && text(n.text, null)) notices.push(n.text);
    return {
      part: "knee", title: single ? "무릎 MRI 학습용 분석 결과 (한 장)" : "무릎 MRI 학습용 분석 결과 (넘기며 분석)", status: "evaluated", meta,
      groups: [{ key: "knee", title: single ? "ACL (전방십자인대) · 시상면 한 장" : "ACL (전방십자인대) · 시상면 여러 장", findings }],
      overlay: kneeOverlay(single, vis, cap),
      explain: items.length ? { mode: "fixed", items, disabledReason: null } : { mode: "none", items: [], disabledReason: "해설이 없습니다." },
      notices,
    };
  }

  function toDisplayModel(part, out) {
    if (part === "knee_screen") return fromKneeScreen(out);
    if (part === "knee") return fromKnee(out);
    if (part === "brain") return fromBrain(out);
    throw new Error("unknown part");
  }

  return { toDisplayModel, fromBrain, fromKnee, fromKneeScreen, score01 };
});
