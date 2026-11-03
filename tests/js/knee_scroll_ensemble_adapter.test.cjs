// 앙상블(B+v4) 결과 표시 어댑터 시험(MSN-20261102-004). 실행: node tests/js/knee_scroll_ensemble_adapter.test.cjs
const assert = require("assert");
const path = require("path");
const A = require(path.join(__dirname, "../../assets/js/screen-result-adapter.js"));
let n = 0;
const t = (name, fn) => { fn(); n += 1; console.log("OK", name); };
const TAU = 0.4798988972937373;

function ok(score, extra = {}) {
  return {
    score_mode: "ensemble", analysis_status: "evaluated", model_kind: "volume", latency_ms: 1234, device: "cpu",
    capture: { used_count: 15, unique_count: 18, used_sequences: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15] },
    acl: { label_ko: "앙상블(B+v4) · 학습용 참고 점수", display_score: score, raw_score: score, display_text: `${(score * 100).toFixed(1)}점 (앙상블 B+v4 · 학습용 참고 점수)`,
      reference: { threshold: TAU, threshold_display: "47.99점", position: score >= TAU ? "above" : "below", text: "고정 실험 참고선 47.99점보다 위 — 화면 입력에서의 정확도는 확인하지 않았습니다." } },
    ensemble: { status: "ok", n_valid: 14, required: 3 },
    visualization: { status: "shown", kind: "input_crop_location", overlay_png_base64: "AAAA", relation: "ACL 입력 크롭 위치 참고 — 병변 위치나 앙상블 점수의 원인 설명이 아닙니다.",
      localization: { status: "shown", frame_ordinal: 8, frame_sequence: 8 } },
    explanations: [{ title: "앙상블(B+v4) 참고 점수 해설", text: "해설" }],
    notices: [{ text: "앙상블(B+v4) 점수는 손상 확률이 아니며 의료 진단이 아닙니다." }], warnings: [], ...extra,
  };
}

t("평가됨: 점수는 '점' 단위·참고선 47.99점·위치 그림은 입력 크롭 위치", () => {
  const m = A.toDisplayModel("knee_screen", ok(0.7123));
  const f = m.groups[0].findings[0];
  assert.strictEqual(m.status, "evaluated");
  assert.strictEqual(f.score, 0.7123);
  assert.strictEqual(f.unit, "points");
  assert.ok(!/%/.test(f.note));
  assert.ok(m.meta.some((x) => x.label === "고정 실험 참고선" && /47\.99점 · 점수가 참고선보다 위/.test(x.value)));
  assert.ok(m.overlay.src && /입력 크롭 위치/.test(m.overlay.note) && /원인 설명이 아닙니다/.test(m.overlay.note));
  assert.strictEqual(m.explain.mode, "fixed");
});

t("미분석: 점수 없음 + 서버 이유, 0점·B 점수로 바꾸지 않음, 그림·해설 없음", () => {
  const out = ok(0.5, { analysis_status: "not_evaluated", ensemble: { status: "unavailable", n_valid: 2, required: 3 },
    acl: { display_score: null, not_evaluated_text: "앙상블 점수를 계산하지 못했습니다(예: 유효 크롭 2/15장, 필요3장). 기존 B를 선택해 다시 분석할 수 있습니다." },
    visualization: { status: "not_evaluated", kind: "none" }, explanations: [] });
  const m = A.toDisplayModel("knee_screen", out);
  const f = m.groups[0].findings[0];
  assert.strictEqual(m.status, "not_evaluated");
  assert.strictEqual(f.score, null);
  assert.strictEqual(f.status, "not_evaluated");
  assert.ok(/2\/15/.test(f.reason));
  assert.strictEqual(m.overlay.src, null);
  assert.strictEqual(m.explain.mode, "none");
  assert.ok(!m.meta.some((x) => x.label === "고정 실험 참고선"));
});

t("이상한 점수(문자열·NaN·범위 밖·status 불일치)는 막대로 쓰지 않는다", () => {
  for (const bad of ["0.7", NaN, 1.5, -0.1, null]) {
    const f = A.toDisplayModel("knee_screen", ok(0.7, { acl: { display_score: bad } })).groups[0].findings[0];
    assert.strictEqual(f.score, null, String(bad));
  }
  const f = A.toDisplayModel("knee_screen", ok(0.7, { ensemble: { status: "unavailable" } })).groups[0].findings[0];
  assert.strictEqual(f.score, null);
});

t("기존 B 응답(score_mode 없음/legacy)은 이전 표시 그대로", () => {
  for (const mode of [undefined, "legacy"]) {
    const m = A.toDisplayModel("knee_screen", { score_mode: mode, model_kind: "volume", analysis_status: "evaluated",
      acl: { display_score: 0.873, display_text: "87.3/100 (보정 전 · 학습용 참고 점수)", score_kind: "raw" }, capture: { used_count: 15, received_count: 15 } });
    const f = m.groups[0].findings[0];
    assert.strictEqual(f.score, 0.873);
    assert.strictEqual(f.unit, undefined);
    assert.ok(/넘기며 분석\)$/.test(m.title));
  }
});

t("앙상블 응답의 알 수 없는 그림 kind 는 숨긴다(CAM 오인 방지)", () => {
  const m = A.toDisplayModel("knee_screen", ok(0.6, { visualization: { status: "shown", kind: "grad_cam", overlay_png_base64: "AAAA" } }));
  assert.strictEqual(m.overlay.src, null);
});

console.log(`all ${n} passed`);
