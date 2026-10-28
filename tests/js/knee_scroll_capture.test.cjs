// 넘기며 분석 수집기·공통 어댑터 순수 계산 시험. 실행: node tests/js/knee_scroll_capture.test.cjs
const assert = require("assert");
const path = require("path");
const K = require(path.join(__dirname, "../../assets/js/knee-scroll-capture.js"));
const A = require(path.join(__dirname, "../../assets/js/screen-result-adapter.js"));
let n = 0;
const t = (name, fn) => { fn(); n += 1; console.log("OK", name); };
const N = K.PROFILE.sig * K.PROFILE.sig;
const flat = (v) => new Uint8Array(N).fill(v);
const patch = (base, v, from, to) => { const a = Uint8Array.from(base); for (let i = from; i < to; i++) a[i] = v; return a; };

t("서명 차이: 같은 그림 0, 전체 밝기 차이 반영", () => {
  assert.deepStrictEqual(K.sigDiff(flat(10), flat(10)), { mad: 0, frac: 0 });
  const d = K.sigDiff(flat(0), flat(51));
  assert.ok(Math.abs(d.mad - 0.2) < 1e-9 && d.frac === 1);
});

t("첫 장은 두 번 연속 같은 화면일 때 채택(넘기는 중간 그림 제외)", () => {
  let r = K.step(null, flat(10));
  assert.strictEqual(r.accept, false);
  r = K.step(r.state, flat(10));
  assert.strictEqual(r.accept, true);
  r = K.step(r.state, flat(10));                       // 같은 장 계속 → 새 후보 아님
  assert.strictEqual(r.accept, false);
});

t("전환 중간 프레임(계속 변함)은 채택하지 않고, 멈추면 채택", () => {
  let r = K.step(null, flat(10)); r = K.step(r.state, flat(10));           // 첫 장
  r = K.step(r.state, flat(60)); assert.strictEqual(r.accept, false);      // 움직이는 중
  r = K.step(r.state, flat(120)); assert.strictEqual(r.accept, false);
  r = K.step(r.state, flat(120)); assert.strictEqual(r.accept, true);      // 멈춤 → 새 장
});

t("작은 국소 변화(2% 이상 화소)도 새 장, 아주 작은 노이즈는 무시", () => {
  const base = flat(100);
  let r = K.step(null, base); r = K.step(r.state, base);
  const small = patch(base, 140, 0, Math.ceil(N * 0.03));                  // 3% 화소가 바뀜(인접 장)
  r = K.step(r.state, small); r = K.step(r.state, small);
  assert.strictEqual(r.accept, true);
  const noise = patch(small, 141, 0, 10);                                  // 10화소 1단계 노이즈
  r = K.step(r.state, noise); r = K.step(r.state, noise);
  assert.strictEqual(r.accept, false);
});

t("단계·크기 규칙: 10 enough · 15 recommended · 20 max, 최대 변 512 축소만", () => {
  assert.deepStrictEqual([9, 10, 15, 20].map(K.phase), ["collecting", "enough", "recommended", "max"]);
  assert.deepStrictEqual(K.targetSize(1024, 768), { w: 512, h: 384 });
  assert.deepStrictEqual(K.targetSize(300, 200), { w: 300, h: 200 });   // 확대 없음
});

const VOL_BOX = { status: "shown", kind: "anatomical_box", overlay_png_base64: "QQ==", overlay_frame_sequence: 7, note: "ACL 대략적 위치 안내 · 병변 위치나 분류 모델의 판단 근거가 아닙니다.",
  title: "ACL 대략적 위치 안내", legend: "ACL이 포함될 수 있는 참고 영역 — 병변 위치 아님", relation: "점수는 여러 장 분류 모델, 박스는 별도 ACL 위치 모델의 결과입니다. 박스가 점수의 원인이나 손상 위치를 뜻하지 않습니다.",
  localization: { status: "shown", frame_ordinal: 8, frame_sequence: 7, box: { x: 0.35, y: 0.3, width: 0.3, height: 0.2 } } };

t("어댑터: 넘기며 분석 응답 → 실제 점수 막대·같은 패널 해설·ACL 위치 박스(장 순번·분류와 위치 구분)", () => {
  const out = { model_kind: "volume", latency_ms: 812, device: "cpu",
    capture: { received_count: 17, used_count: 15, plane: "sagittal", scope: "full_slice" },
    acl: { label_ko: "ACL 손상 관련 모델 점수", display_score: 0.873, score_kind: "raw", decision: "score_only", display_text: "87.3/100 (보정 전 · 학습용 참고 점수)" },
    visualization: VOL_BOX,
    explanations: [{ title: "ACL 모델 점수 해설", text: "템플릿 해설" }], warnings: [{ text: "같은 화면 2장을 빼고" }], notices: [{ text: "학습용 시연" }] };
  const m = A.toDisplayModel("knee_screen", out);
  const f = m.groups[0].findings[0];
  assert.strictEqual(f.score, 0.873);
  assert.ok(f.note.includes("87.3/100") && f.note.includes("보정 전"));
  assert.ok(m.meta[0].value.includes("15장") && m.meta[0].value.includes("17"));
  assert.ok(m.overlay.src.startsWith("data:image/png;base64,"));
  assert.strictEqual(m.overlay.alt, "ACL 대략적 위치 안내");
  for (const s of ["ACL 대략적 위치 안내", "병변 위치 아님", "분석에 쓴 15장 중 8번째 장(수집 순번 7)", "별도 ACL 위치 모델", "손상 위치를 뜻하지 않습니다"]) assert.ok(m.overlay.note.includes(s), s);
  assert.ok(!/열지도|Grad-CAM|%/.test(m.overlay.note));                    // 위치 박스에 열지도 이름·퍼센트 없음
  assert.deepStrictEqual(m.explain, { mode: "fixed", items: [{ title: "ACL 모델 점수 해설", text: "템플릿 해설" }], disabledReason: null });
  const bad = A.toDisplayModel("knee_screen", { acl: { display_score: NaN } });
  assert.strictEqual(bad.groups[0].findings[0].score, null);              // 0% 로 바꾸지 않음
});

t("어댑터: 넘기며 분석 위치 보류·미요청·이전 형식 CAM·알 수 없는 kind → 그림 없음, 점수는 그대로", () => {
  const base = { model_kind: "volume", capture: { received_count: 15, used_count: 15, plane: "sagittal", scope: "full_slice" },
    acl: { display_score: 0.5, score_kind: "raw", display_text: "50.0/100 (보정 전 · 학습용 참고 점수)" } };
  const withheld = A.toDisplayModel("knee_screen", Object.assign({}, base, { visualization: { status: "withheld", kind: "none", overlay_png_base64: null,
    note: "이 장에서 ACL 위치를 표시하지 못했습니다. 정상이라는 뜻은 아닙니다.", localization: { status: "not_found", box: null } } }));
  assert.strictEqual(withheld.overlay.src, null);
  assert.ok(withheld.overlay.note.includes("정상이라는 뜻은 아닙니다"));
  assert.strictEqual(withheld.groups[0].findings[0].score, 0.5);
  const nr = A.toDisplayModel("knee_screen", Object.assign({}, base, { visualization: { status: "not_requested", kind: "none", overlay_png_base64: null, localization: { status: "not_requested" } } }));
  assert.deepStrictEqual([nr.overlay.src, nr.overlay.note], [null, null]);
  // 이전 형식(kind 없음) 볼륨 CAM PNG 는 새 위치 박스로 오인해 보이지 않는다
  const old = A.toDisplayModel("knee_screen", Object.assign({}, base, { visualization: { status: "shown", overlay_png_base64: "QQ==", overlay_frame_sequence: 2, note: "모델이 참고한 영역" } }));
  assert.strictEqual(old.overlay.src, null);
  assert.ok(old.overlay.note.includes("이전 형식"));
  for (const vis of [Object.assign({}, VOL_BOX, { kind: "grad_cam" }), Object.assign({}, VOL_BOX, { kind: "lesion_mask" }),
                     Object.assign({}, VOL_BOX, { localization: { status: "not_found" } }), Object.assign({}, VOL_BOX, { localization: null })]) {
    assert.strictEqual(A.toDisplayModel("knee_screen", Object.assign({}, base, { visualization: vis })).overlay.src, null, JSON.stringify(vis.kind));
  }
});

t("어댑터: 한 장(single_2d) 응답 → 제목·모델·그룹이 '한 장', raw 점수·보정 전 표시, 여러 장 문구 없음", () => {
  const out = { model_kind: "single_2d", latency_ms: 95, device: "cpu",
    capture: { received_count: 1, used_count: 1, plane: "sagittal", scope: "full_slice" },
    acl: { label_ko: "ACL 손상 관련 모델 점수", display_score: 0.724, score_kind: "raw", decision: "score_only", band: null, calibrated_score: null, display_text: "72.4/100 (보정 전 · 학습용 참고 점수)" },
    visualization: { status: "shown", overlay_png_base64: "QQ==", overlay_frame_sequence: 0, note: "모델이 참고한 영역(Grad-CAM, 같은 한 장 입력·같은 점수 logit)" },
    explanations: [{ title: "ACL 한 장 모델 참고 점수 해설", text: "한 장 해설" }], warnings: [], notices: [{ text: "학습용 시연" }] };
  const m = A.toDisplayModel("knee_screen", out);
  assert.strictEqual(m.title, "무릎 MRI 학습용 분석 결과 (한 장)");
  assert.strictEqual(m.groups[0].title, "ACL (전방십자인대) · 시상면 한 장");
  assert.ok(m.meta.some((x) => x.label === "모델" && x.value === "ACL 시상면 한 장 모델"));
  assert.ok(m.meta.some((x) => x.value === "1장"));
  assert.strictEqual(m.groups[0].findings[0].score, 0.724);
  assert.ok(m.groups[0].findings[0].note.includes("보정 전 · 정상/파열 판정 아님"));
  assert.ok(!JSON.stringify(m).includes("여러 장") && !m.overlay.note.includes("캡처 순번"));
  assert.strictEqual(m.explain.items[0].text, "한 장 해설");
  // 한 장 CAM 은 '모델 참고 열지도'로 유지(이전 형식 kind 없음도 같은 이름) — 위치 박스 문구 없음
  assert.ok(m.overlay.src && m.overlay.alt === "모델 참고 열지도(Grad-CAM)" && m.overlay.note.startsWith("모델 참고 열지도(Grad-CAM)"));
  assert.ok(!m.overlay.note.includes("ACL 대략적 위치"));
  const cam = A.toDisplayModel("knee_screen", Object.assign({}, out, { visualization: { status: "shown", kind: "grad_cam", overlay_png_base64: "QQ==", title: "모델 참고 열지도(Grad-CAM)",
    note: "모델 점수에 대한 참고 반응이며 병변 위치·윤곽이 아닙니다." } }));
  assert.ok(cam.overlay.src && cam.overlay.note.includes("병변 위치·윤곽이 아닙니다"));
  const noact = A.toDisplayModel("knee_screen", Object.assign({}, out, { visualization: { status: "no_activation", kind: "grad_cam", overlay_png_base64: null } }));
  assert.ok(noact.overlay.src === null && noact.overlay.note.includes("정상이라는 뜻은 아닙니다"));
  assert.strictEqual(A.toDisplayModel("knee_screen", Object.assign({}, out, { visualization: Object.assign({}, VOL_BOX) })).overlay.src, null);   // 한 장에 위치 박스 kind 는 표시 안 함
  const vol = A.toDisplayModel("knee_screen", Object.assign({}, out, { model_kind: "volume" }));
  assert.strictEqual(vol.title, "무릎 MRI 학습용 분석 결과 (넘기며 분석)");   // 여러 장은 기존 제목 그대로
  assert.strictEqual(vol.overlay.src, null);                                  // kind 없는 CAM 은 넘기며 분석에서 숨김
  const brain = A.toDisplayModel("brain", { overlay_png_base64: "QQ==" });
  assert.ok(!brain.overlay || brain.overlay.alt === undefined);               // 뇌는 alt 를 주지 않음 → 템플릿 기본값 유지
});

t("미세 변화 허용 한계: 100→101(평균 1/255) 안정 = 다른 장으로 채택, 10화소 1단계 노이즈 = 같은 장", () => {
  const a = flat(100), b = flat(101);
  let st = null, acc = 0;
  for (const sig of [a, a, b, b, b]) { const r = K.step(st, sig); st = r.state; if (r.accept) acc++; }
  assert.strictEqual(acc, 2);
});

t("어댑터: 서버 비교선·참고구간을 그대로 표시, 계산하지 않음, 진단 아님은 항상", () => {
  const base = { model_kind: "single_2d", capture: { used_count: 1, received_count: 1, plane: "sagittal", scope: "full_slice" },
    visualization: { status: "no_activation" }, explanations: [{ title: "해설", text: "t" }], notices: [] };
  const m = A.toDisplayModel("knee_screen", Object.assign({}, base, { acl: { display_score: 0.724, score_kind: "raw", decision: "reference_compared", band: "high",
    display_text: "72.4/100 (보정 전 · 학습용 참고 점수)", reference: { label: "데이터셋 기준 비교선", threshold: 0.17, position: "above", band_text: "데이터셋 손상 라벨 쪽 참고구간", input_condition: "assumed_dataset_mid_slice" } } }));
  const meta = m.meta.find((x) => x.label === "데이터셋 기준 비교선");
  assert.ok(meta && meta.value.includes("17.0") && meta.value.includes("위") && meta.value.includes("손상 라벨 쪽 참고구간"), JSON.stringify(m.meta));
  assert.ok(m.notices.some((n) => n.includes("가정한 비교")));
  assert.ok(m.groups[0].findings[0].note.includes("의료 진단 아님"));
  const cal = A.toDisplayModel("knee_screen", Object.assign({}, base, { acl: { display_score: 0.6, score_kind: "calibrated", display_text: "60.0/100 (내부 자료 보정 참고값)" } }));
  assert.ok(cal.groups[0].findings[0].note.includes("의료 진단 아님") && !cal.groups[0].findings[0].note.includes("보정 전"));   // 보정값이어도 진단 아님 유지
  const none = A.toDisplayModel("knee_screen", Object.assign({}, base, { acl: { display_score: 0.6, score_kind: "raw", reference: null } }));
  assert.ok(!none.meta.some((x) => /비교선|참고선/.test(x.label)));
});

t("다듬어짐 합치기(v3) 순수 결정: 기준 장 고정·0.7초·기준 변화의 40% 이하만 교체, 작은 변화 연속은 새 장", () => {
  assert.strictEqual(K.PROFILE.id, "capture_profile_v3");
  const a = flat(100), big = flat(130), ref = patch(big, 140, 0, 1200);   // big: 큰 변화 0.118, ref: 그 장 대비 약 0.0115(=약 10%) 다듬어짐
  let d = K.settleDecision(null, a, null, 0);
  assert.deepStrictEqual([d.replace, d.anchor.transMad], [false, K.PROFILE.madNew]);   // 수집 첫 장 = 기준 장
  d = K.settleDecision(d.anchor, big, a, 1000);
  assert.strictEqual(d.replace, false);                                           // 큰 변화 = 새 장, 새 기준
  const anc = d.anchor;
  assert.strictEqual(K.settleDecision(anc, ref, big, 1250).replace, true);        // 0.25초 다듬어짐 → 교체
  assert.strictEqual(K.settleDecision(anc, ref, big, 1650).replace, true);        // 0.65초(기준 시각 고정)
  assert.strictEqual(K.settleDecision(anc, ref, big, 1750).replace, false);       // 0.75초 = 창 밖 → 새 장
  assert.strictEqual(K.settleDecision({ ...anc, transMad: 0.02 }, ref, big, 1250).replace, false);   // 기준 변화의 40% 초과 → 새 장
  d = K.settleDecision(null, flat(101), flat(100), 1000);
  assert.strictEqual(d.replace, false);
  assert.strictEqual(d.anchor, null);                                             // 작은 변화로 채택된 장은 기준 장이 아님
});

// 실제 화면 공유 시범 재현: 장을 넘긴 직후 그림이 한 번 더 다듬어지는 캡처, 분석 화면 가려짐
async function settleScenario() {
  let interval, now = 0, cur = 50;
  const real = { set: global.setInterval, clear: global.clearInterval, now: Date.now };
  global.setInterval = (f) => { interval = f; return 1; };
  global.clearInterval = () => {};
  Date.now = () => now;
  const img = () => ({ data: new Uint8ClampedArray(64 * 64 * 4).fill(cur) });
  const source = { width: 64, height: 64, getContext: () => ({ getImageData: img }) };
  Object.defineProperty(global, "crypto", { value: { subtle: { digest: (_a, g) => { const o = new Uint8Array(32); o[0] = g[8]; return Promise.resolve(o.buffer); } } }, configurable: true });
  global.document = { visibilityState: "visible", createElement: () => ({ getContext: () => ({ drawImage() {}, getImageData: img }), toBlob: (cb) => cb(new Blob(["x"])) }) };
  URL.createObjectURL = () => "blob:x"; URL.revokeObjectURL = () => {};
  const c = K.createCollector({ getCanvas: () => source, getKey: () => "k", onChange() {}, onDone() {} });
  c.start();
  const show = async (v, at) => { cur = v; now = at; await interval(); now = at + 100; await interval(); };
  await show(50, 0);          // 장 1
  await show(80, 1000);       // 장 2(큰 변화)
  const hash2 = c.frames[1].hash;
  await show(81, 1300);       // 큰 변화 0.3초 뒤 미세하게 다듬어진 같은 장 → 바꿔 끼움
  const afterSettle = { n: c.frames.length, replaced: c.frames[1].hash !== hash2 };
  await show(82, 3000);       // 기준 창(0.7초) 밖에 온 비슷한 다른 장 → 새 장
  const afterLater = c.frames.length;
  global.document.visibilityState = "hidden";
  await show(120, 4000);      // 가려진 동안: 확인 안 함·무변화로 끝내지 않음
  const hidden = { n: c.frames.length, seen: c.hiddenSeen, running: c.running };
  global.document.visibilityState = "visible";
  await show(120, 6000);      // 다시 보이면 이어서(가려진 동안을 무변화로 세지 않음)
  const back = { n: c.frames.length, running: c.running };
  global.setInterval = real.set; global.clearInterval = real.clear; Date.now = real.now;
  return { afterSettle, afterLater, hidden, back };
}

// 전체 collector 통제 시나리오(Codex CAPTURE-V2-001·003): 비상수 패턴·실제 SHA 경로, 입력 장 정답을 알고 비교
function harness() {
  let tick, now = 0, cur = new Uint8Array(4096).fill(100), reason = null, vis = null;
  const real = { set: global.setInterval, clear: global.clearInterval, now: Date.now, crypto: global.crypto };
  global.setInterval = (f) => { tick = f; return 1; }; global.clearInterval = () => {}; Date.now = () => now;
  const data = () => { const d = new Uint8ClampedArray(4096 * 4); for (let i = 0; i < 4096; i++) { d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = cur[i]; d[i * 4 + 3] = 255; } return { data: d }; };
  const ctx = () => ({ drawImage() {}, getImageData: data });
  Object.defineProperty(global, "crypto", { value: require("node:crypto").webcrypto, configurable: true });
  global.document = { visibilityState: "visible", createElement: () => ({ width: 64, height: 64, getContext: ctx, toBlob: (cb) => cb(new Blob([cur])) }),
    addEventListener: (_n, f) => { vis = f; }, removeEventListener: () => { vis = null; } };
  URL.createObjectURL = () => "blob:t"; URL.revokeObjectURL = () => {};
  const c = K.createCollector({ getKey: () => "k", getCanvas: () => ({ width: 64, height: 64, getContext: ctx }), onChange() {}, onDone: (r) => { reason = r; } });
  c.start();
  return {
    c, get reason() { return reason; },
    async show(sig, at) { cur = sig; now = at; await tick(); now = at + 100; await tick(); },
    async tickAt(at) { now = at; await tick(); },
    setVisible(v, at) { now = at; document.visibilityState = v ? "visible" : "hidden"; if (vis) vis(); },
    restore() { global.setInterval = real.set; global.clearInterval = real.clear; Date.now = real.now; Object.defineProperty(global, "crypto", { value: real.crypto, configurable: true }); },
  };
}
const pattern = (o) => Uint8Array.from({ length: 4096 }, (_, i) => 60 + Math.floor((i % 64) / 8) * 8 + Math.floor(i / 512) * 4 + o);
const refine = (sig, frac, amt) => { const a = Uint8Array.from(sig); for (let i = 0; i < Math.floor(4096 * frac); i++) a[i] += amt; return a; };

async function collectorCases() {
  const out = {};
  for (const [name, gap, n] of [["작은 변화 1초 간격", 1000, 15], ["작은 변화 2초 간격", 2000, 15], ["작은 변화 0.5초 간격", 500, 15]]) {
    const h = harness();
    for (let i = 0; i < n; i++) await h.show(pattern(i), i * gap);
    out[name] = { got: h.c.frames.length, merged: h.c.merged }; h.c.clear(); h.restore();
  }
  { // 3% 국소 변화
    const h = harness(); await h.show(pattern(0), 0); await h.show(refine(pattern(0), 0.03, 40), 1000);
    out["3% 국소 변화"] = { got: h.c.frames.length }; h.c.clear(); h.restore();
  }
  { // 실제 캡처 모양: 1초마다 큰 변화(0.0196) + 0.25초(그 장 대비 0.0047)·0.6초(0.0024) 다듬어짐 → 장 수 = 넘긴 수+1
    const h = harness(); await h.show(pattern(0), 0);
    for (let k = 1; k <= 14; k++) { const t0 = k * 1000, sl = pattern(k * 5); await h.show(sl, t0); await h.show(refine(sl, 0.10, 12), t0 + 250); await h.show(refine(sl, 0.10, 6), t0 + 600); }
    out["실제 캡처 모양 14번 넘김"] = { got: h.c.frames.length, merged: h.c.merged }; h.c.clear(); h.restore();
  }
  { // 연장 없음: 큰 변화(1.1초 채택) 뒤 0.25·0.5초 다듬어짐(교체) → 0.9초에 온 작은 변화는 고정 창(0.7초) 밖이라 새 장.
    //  (교체 때마다 시각을 늘렸다면 직전 교체 0.4초 뒤라 합쳐졌을 것)
    const h = harness(); await h.show(pattern(0), 0); const sl = pattern(20); await h.show(sl, 1000);
    await h.show(refine(sl, 0.10, 12), 1250); await h.show(refine(sl, 0.10, 6), 1500); await h.show(sl, 1900);
    out["교체로 기간 연장 안 함"] = { got: h.c.frames.length, merged: h.c.merged }; h.c.clear(); h.restore();
  }
  { // 숨김 이벤트: 1장 → hidden(1s) → 20초 뒤 visible → 수집 유지·경고·이어서 채택
    const h = harness(); await h.show(pattern(0), 0); h.setVisible(false, 1000); h.setVisible(true, 21000);
    await h.show(pattern(10), 21000);
    out["숨김 이벤트 20초"] = { got: h.c.frames.length, hidden: h.c.hiddenSeen, running: h.c.running, reason: h.reason }; h.c.clear(); h.restore();
  }
  { // 숨김 중 타이머 0개(이벤트도 없음) → 복귀 첫 확인 간격 20초 → 정지로 보고 이어서
    const h = harness(); await h.show(pattern(0), 0); await h.tickAt(20100);
    out["타이머 정지 20초"] = { got: h.c.frames.length, hidden: h.c.hiddenSeen, running: h.c.running, reason: h.reason }; h.c.clear(); h.restore();
  }
  { // 숨김 없이 무변화 15초는 기존처럼 idle 로 끝남(정상 100ms 확인)
    const h = harness(); await h.show(pattern(0), 0); for (let t = 200; t <= 15400; t += 100) await h.tickAt(t);
    out["보이는 채 무변화 15초"] = { running: h.c.running, reason: h.reason }; h.c.clear(); h.restore();
  }
  return out;
}

// ── 비동기: 정지·초기화가 진행 중인 해시/인코딩 결과를 막는지(Codex STEP2-004) ──
async function collectorScenario(pauseAt) {
  let interval, release;
  const realSet = global.setInterval, realClear = global.clearInterval;
  global.setInterval = (f) => { interval = f; return 1; };
  global.clearInterval = () => {};
  const img = () => ({ data: new Uint8ClampedArray(64 * 64 * 4).fill(50) });
  const source = { width: 64, height: 64, getContext: () => ({ getImageData: img }) };
  Object.defineProperty(global, "crypto", { value: { subtle: { digest: () => pauseAt === "hash" ? new Promise((r) => { release = () => r(new Uint8Array(32).buffer); }) : Promise.resolve(new Uint8Array(32).buffer) } }, configurable: true });
  global.document = { createElement: () => ({ getContext: () => ({ drawImage() {}, getImageData: img }), toBlob: (cb) => (pauseAt === "encode" ? (release = () => cb(new Blob(["x"]))) : cb(new Blob(["x"]))) }) };
  URL.createObjectURL = () => "blob:x"; URL.revokeObjectURL = () => {};
  const c = K.createCollector({ getCanvas: () => source, getKey: () => "k", onChange() {}, onDone() {} });
  c.start();
  await interval();                         // 첫 확인(안정 대기)
  const pending = interval();               // 두 번째 확인 → 채택 → 해시/인코딩에서 멈춤
  await new Promise((r) => setImmediate(r));
  return { c, pending, release: () => release && release(), restore: () => { global.setInterval = realSet; global.clearInterval = realClear; } };
}

(async () => {
  {
    const r = await collectorCases();
    const exp = { "작은 변화 1초 간격": { got: 15, merged: 0 }, "작은 변화 2초 간격": { got: 15, merged: 0 },
      "작은 변화 0.5초 간격": { got: 14, merged: 1 },   // 알려진 한계: 수집 시작 0.7초 안의 아주 작은 변화 1번은 첫 장과 합쳐짐(합친 횟수 화면 표시)
      "3% 국소 변화": { got: 2 }, "실제 캡처 모양 14번 넘김": { got: 15, merged: 28 },
      "교체로 기간 연장 안 함": { got: 3, merged: 2 },
      "숨김 이벤트 20초": { got: 2, hidden: true, running: true, reason: null },
      "타이머 정지 20초": { got: 1, hidden: true, running: true, reason: null },
      "보이는 채 무변화 15초": { running: false, reason: "idle" } };
    assert.deepStrictEqual(r, exp, JSON.stringify(r));
    n += 1; console.log("OK 전체 collector: 비슷한 다른 장 보존(1·2초 15장)·국소 3%·실제 캡처 다듬어짐 합치기(14번→15장)·교체 연장 없음·숨김 이벤트/타이머 정지 복구·보이는 무변화 idle");
  }
  {
    const r = await settleScenario();
    assert.deepStrictEqual(r.afterSettle, { n: 2, replaced: true }, JSON.stringify(r));
    assert.strictEqual(r.afterLater, 3);
    assert.deepStrictEqual(r.hidden, { n: 3, seen: true, running: true });
    assert.deepStrictEqual(r.back, { n: 4, running: true });
    n += 1; console.log("OK 실제 공유 재현: 큰 변화 직후 다듬어진 같은 장은 바꿔 끼움·창 밖 비슷한 장은 새 장·가려진 동안 멈춤(무변화 정지 없음)·다시 보이면 이어서");
  }
  for (const pauseAt of ["hash", "encode"]) {
    for (const action of ["stop", "clear"]) {
      const s = await collectorScenario(pauseAt);
      const before = s.c.frames.length;
      if (action === "stop") s.c.stop("user"); else s.c.clear();
      s.release(); await s.pending; await new Promise((r) => setImmediate(r));
      assert.strictEqual(before, 0);
      assert.strictEqual(s.c.frames.length, 0, `${pauseAt} 대기 중 ${action} 뒤 장이 추가됨`);
      assert.strictEqual(s.c.running, false);
      s.restore();
      n += 1; console.log("OK", `${pauseAt} 대기 중 ${action} → 늦게 끝난 작업이 목록에 안 들어감`);
    }
  }
  console.log(`\n${n}개 통과`);
})().catch((e) => { console.error(e); process.exit(1); });
