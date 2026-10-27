// 무릎 학습 화면의 핵심 규칙(assets/js/knee-learning-core.js) 시험. 실행: node --test tests/js
// 브라우저 없이 좌표 변환·박스 편집·제출값·서버 응답 해석을 확인한다. 채점 계산은 여기에 없다(서버 몫).
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const Core = require(process.env.KNEE_CORE_PATH || path.join(__dirname, "..", "..", "assets", "js", "knee-learning-core.js"));
const close = (a, b, tol = 1e-9) => assert.ok(Math.abs(a - b) <= tol, `${a} !≈ ${b}`);

test("화면 좌표 → 영상 기준 0~1: 확대·이동·화면 크기와 무관", () => {
  const base = { left: 100, top: 50, width: 320, height: 384 };
  const p = Core.pointToNormalized(100 + 320 * 0.25, 50 + 384 * 0.75, base);
  close(p.x, 0.25); close(p.y, 0.75);
  // 같은 상대 위치를 3배 확대·다른 위치로 옮긴 상자에서 눌러도 같은 좌표
  const zoomed = { left: -400, top: 900, width: 960, height: 1152 };
  const q = Core.pointToNormalized(zoomed.left + zoomed.width * 0.25, zoomed.top + zoomed.height * 0.75, zoomed);
  close(q.x, 0.25); close(q.y, 0.75);
});

test("영상 밖을 눌러도 좌표는 0~1 안으로 잘린다", () => {
  const rect = { left: 10, top: 10, width: 100, height: 200 };
  assert.deepEqual(Core.pointToNormalized(-500, 9999, rect), { x: 0, y: 1 });
  assert.deepEqual(Core.pointToNormalized(9999, -500, rect), { x: 1, y: 0 });
});

test("크기가 0이거나 없는 상자에서는 좌표를 만들지 않는다", () => {
  assert.equal(Core.pointToNormalized(1, 1, { left: 0, top: 0, width: 0, height: 10 }), null);
  assert.equal(Core.pointToNormalized(1, 1, null), null);
});

test("박스 만들기: 어느 방향으로 끌어도 왼쪽 위 기준", () => {
  const b = Core.boxFromPoints({ x: 0.7, y: 0.8 }, { x: 0.2, y: 0.3 });
  close(b.x, 0.2); close(b.y, 0.3); close(b.width, 0.5); close(b.height, 0.5);
});

test("이동: 영상 밖으로 나가지 않고 크기는 그대로", () => {
  const moved = Core.moveBox({ x: 0.5, y: 0.5, width: 0.4, height: 0.4 }, 0.9, -0.9);
  close(moved.x, 0.6); close(moved.y, 0); close(moved.width, 0.4); close(moved.height, 0.4);
});

test("크기 조절: 반대쪽 변 고정, 최소 크기 유지, 영상 안", () => {
  const start = { x: 0.2, y: 0.2, width: 0.4, height: 0.4 };
  const se = Core.resizeBox(start, "se", { x: 2, y: 2 });
  close(se.x, 0.2); close(se.y, 0.2); close(se.x + se.width, 1); close(se.y + se.height, 1);
  const nw = Core.resizeBox(start, "nw", { x: 0.9, y: 0.9 });   // 반대쪽 변을 넘겨 끌어도 뒤집히지 않는다
  assert.ok(nw.width >= Core.MIN_SIZE - 1e-12 && nw.height >= Core.MIN_SIZE - 1e-12);
  close(nw.x + nw.width, 0.6); close(nw.y + nw.height, 0.6);
  const e = Core.resizeBox(start, "e", { x: 0.1, y: 0.9 });    // e 는 가로만 바뀐다
  close(e.y, 0.2); close(e.height, 0.4); assert.ok(e.width >= Core.MIN_SIZE - 1e-12);
});

test("박스 유효성: NaN·Infinity·음수·밖으로 나간 값·너무 작은 값 거절", () => {
  const ok = { x: 0.1, y: 0.1, width: 0.5, height: 0.5 };
  assert.equal(Core.isBoxValid(ok), true);
  for (const bad of [
    null, { x: NaN, y: 0, width: 0.5, height: 0.5 }, { x: 0, y: 0, width: Infinity, height: 0.5 },
    { x: -0.1, y: 0, width: 0.5, height: 0.5 }, { x: 0.6, y: 0, width: 0.5, height: 0.5 },
    { x: 0, y: 0.6, width: 0.5, height: 0.5 }, { x: 0, y: 0, width: 0.01, height: 0.5 },
    { x: 0, y: 0, width: 0.5, height: -0.2 }, { x: "0.1", y: 0, width: 0.5, height: 0.5 },
  ]) assert.equal(Core.isBoxValid(bad), false, JSON.stringify(bad));
});

test("제출값: 6자리 문자열, 반올림해도 이미지 밖으로 나가지 않는다", () => {
  const fields = Core.toSubmitFields({ x: 0.3333333333, y: 0.5, width: 0.6666666667, height: 0.5 });
  assert.equal(fields.roi_type, "box");
  for (const key of ["x", "y", "width", "height"]) assert.match(fields[key], /^\d\.\d{6}$/);
  assert.ok(Number(fields.x) + Number(fields.width) <= 1 + 1e-12);
  assert.ok(Number(fields.y) + Number(fields.height) <= 1 + 1e-12);
  assert.equal(Core.toSubmitFields({ x: 0.9, y: 0.9, width: 0.5, height: 0.5 }), null);   // 이미 밖이면 보내지 않는다
  assert.equal(Core.toSubmitFields(null), null);
});

test("제출값 무작위: 어떤 유효 박스도 밖으로 나가지 않고 폭·높이는 양수", () => {
  let seed = 12345;
  const rand = () => ((seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296);
  for (let i = 0; i < 2000; i++) {
    const x = rand() * 0.9, y = rand() * 0.9;
    const box = { x, y, width: Math.min(1 - x, 0.02 + rand() * 0.98), height: Math.min(1 - y, 0.02 + rand() * 0.98) };
    if (!Core.isBoxValid(box)) continue;
    const f = Core.toSubmitFields(box);
    assert.ok(f, JSON.stringify(box));
    assert.ok(Number(f.width) > 0 && Number(f.height) > 0);
    assert.ok(Number(f.x) + Number(f.width) <= 1 + 1e-9 && Number(f.y) + Number(f.height) <= 1 + 1e-9);
    assert.ok(Math.abs(Number(f.x) - box.x) <= 1e-6 && Math.abs(Number(f.y) - box.y) <= 1e-6);
  }
});

test("방향키: 이동은 영상 안에서만, shift 는 오른쪽·아래 변 크기 조절", () => {
  const box = { x: 0.5, y: 0.5, width: 0.5, height: 0.5 };
  assert.deepEqual(Core.nudgeBox(box, "ArrowRight", false), box);   // 이미 오른쪽 끝이라 더 못 움직인다
  const moved = Core.nudgeBox({ x: 0.1, y: 0.1, width: 0.3, height: 0.3 }, "ArrowDown", false);
  close(moved.y, 0.1 + Core.KEY_STEP);
  const grown = Core.nudgeBox({ x: 0.1, y: 0.1, width: 0.3, height: 0.3 }, "ArrowRight", true);
  close(grown.width, 0.3 + Core.KEY_STEP);
  assert.equal(Core.nudgeBox(box, "a", false), box);
});

// ── 서버 응답 해석 ────────────────────────────────────────────────────────────
const graded = (over = {}) => Object.assign({
  ok: true, graded: true, result: "정답", iou: 0.62341, review_idx: 11,
  reference_bbox: { x: 0.3, y: 0.3, width: 0.4, height: 0.4 },
  user_roi_bbox: { x: 0.31, y: 0.3, width: 0.4, height: 0.4 },
  rule: { metric: "iou", version: "v", correct_iou: 0.5, partial_iou: 0.2 }, slice_index: 12, notices: ["a", 3],
}, over);

test("채점됨: 일치/일부 일치/차이 있음 문구와 5자리 IoU", () => {
  const labels = { "정답": "기준 영역과 일치", "부분정답": "일부 일치", "오답": "차이 있음" };
  for (const [result, label] of Object.entries(labels)) {
    const v = Core.interpretGradeResponse(graded({ result }));
    assert.equal(v.kind, "graded"); assert.equal(v.label, label); assert.equal(v.iouText, "0.62341");
    assert.deepEqual(v.notices, ["a"]);   // 문자열이 아닌 안내는 버린다
  }
});

test("graded 가 true 가 아니면 점수가 있어도 정답/오답으로 보이지 않는다", () => {
  for (const flag of [false, "true", 1, null, undefined]) {
    const v = Core.interpretGradeResponse(graded({ graded: flag, not_graded_reason: "no_reference_for_slice" }));
    assert.equal(v.kind, "ungraded", String(flag));
    assert.equal(v.label, undefined);
  }
});

test("모양이 이상한 채점 응답은 채점됨으로 취급하지 않는다", () => {
  for (const bad of [
    graded({ result: "합격" }), graded({ iou: null }), graded({ iou: NaN }), graded({ reference_bbox: null }),
    graded({ reference_bbox: { x: 0.9, y: 0.9, width: 0.5, height: 0.5 } }), null, "text", [],
  ]) assert.equal(Core.interpretGradeResponse(bad).kind, "unknown", JSON.stringify(bad));
});

test("미채점: 사유별 고정 안내, 토큰 문제는 문제 다시 열기, review_idx 는 쓰지 않는다", () => {
  const base = { ok: true, graded: false, result: null, iou: null, review_idx: null };
  const none = Core.interpretGradeResponse(Object.assign({}, base, { not_graded_reason: "no_reference_for_slice", not_graded_message: "서버문구" }));
  assert.equal(none.kind, "ungraded"); assert.match(none.message, /대표 영상을 확인해 주세요/); assert.equal(none.needsReopen, false);
  for (const reason of ["token_invalid", "token_expired", "presentation_changed"]) {
    assert.equal(Core.interpretGradeResponse(Object.assign({}, base, { not_graded_reason: reason })).needsReopen, true, reason);
  }
  const unknownReason = Core.interpretGradeResponse(Object.assign({}, base, { not_graded_reason: "x", not_graded_message: "서버 안내" }));
  assert.equal(unknownReason.message, "서버 안내");
  const noMessage = Core.interpretGradeResponse(Object.assign({}, base, { not_graded_reason: "x" }));
  assert.equal(noMessage.message, "이 제출은 채점하지 않았습니다.");
});

test("HTTP 오류 문구와 재시도 가능 여부", () => {
  assert.match(Core.describeHttpError(401).message, /로그인이 필요합니다/);
  assert.equal(Core.describeHttpError(401).canRetry, true);
  assert.match(Core.describeHttpError(400, "박스는 이미지 안에 있어야 합니다.").message, /박스는 이미지 안에/);
  assert.doesNotMatch(Core.describeHttpError(400, { evil: 1 }).message, /object/);
  assert.equal(Core.describeHttpError(403).canRetry, false);
  assert.equal(Core.describeHttpError(502).canRetry, true);
  assert.equal(Core.describeHttpError(503).canRetry, false);
  assert.match(Core.describeHttpError(0).message, /통신하지 못했습니다/);
  assert.equal(Core.describeHttpError(0).canRetry, true);
});

// ── 문제 열기 응답 ─────────────────────────────────────────────────────────────
const task = (over = {}) => ({ study_idx: 3, knee_task: Object.assign({
  schema_version: "knee-representative-v1", representative_slice_index: 12, display_slice_number: 13, slice_count: 24,
  image_url: "/_images/rep-7.png", image_width: 320, image_height: 384, grading_available: true,
  unavailable_reason: null, problem_token: "tok", task_label: "ACL이 포함된 참고 영역 표시 연습",
}, over) });

test("문제 열기: 정상 응답", () => {
  const t = Core.interpretTask(task());
  assert.equal(t.usable, true); assert.equal(t.sliceIndex, 12); assert.equal(t.displayNumber, 13);
  assert.equal(t.imageWidth, 320); assert.equal(t.problemToken, "tok");
});

test("문제 열기: 채점 불가는 사유 안내만, 토큰·영상을 쓰지 않는다", () => {
  const t = Core.interpretTask(task({ grading_available: false, unavailable_reason: "no_reference_for_slice", problem_token: null, image_url: null }));
  assert.equal(t.usable, false); assert.match(t.message, /승인된 기준 영역이 없어/);
});

test("문제 열기: 위험하거나 이상한 영상 주소·값은 거절", () => {
  for (const bad of [
    { image_url: "//evil.example/x.png" }, { image_url: "http://x/y.png" }, { image_url: "javascript:alert(1)" },
    { image_url: "data:image/png;base64,AAAA" }, { image_url: "" }, { image_url: null },
    { problem_token: "" }, { problem_token: null }, { representative_slice_index: -1 }, { representative_slice_index: 1.5 },
    { image_width: 0 }, { image_height: "384" }, { image_width: 320.5 },
  ]) assert.equal(Core.interpretTask(task(bad)).usable, false, JSON.stringify(bad));
  assert.equal(Core.interpretTask(null).usable, false);
  assert.equal(Core.interpretTask({}).usable, false);
  assert.equal(Core.interpretTask(task({ image_url: "https://cdn.example/x.png" })).usable, true);
  // data: 주소는 시연(demo)에서만
  assert.equal(Core.interpretTask(task({ image_url: "data:image/png;base64,AAAA" }), { allowDataUrl: true }).usable, true);
  assert.equal(Core.interpretTask(task({ image_url: "data:text/html;base64,AAAA" }), { allowDataUrl: true }).usable, false);
});

test("영상 크기 검사: 서버가 알려 준 크기와 다르면 거절", () => {
  assert.equal(Core.imageSizeMatches(320, 384, 320, 384), true);
  assert.equal(Core.imageSizeMatches(384, 320, 320, 384), false);
  assert.equal(Core.imageSizeMatches(640, 768, 320, 384), false);
});

// ── FINDING-SCREEN-017-01/02 및 NOTE: 재시도 문구·영상 동일성·변경 안내·응답 유실 한계 ─────────────────────
const usable = (over = {}) => Core.interpretTask(task(Object.assign({ presentation_id: "0123456789abcdef" }, over)));

test("제시 식별값: 16자리 16진수만 받고 나머지는 없는 것으로 본다", () => {
  assert.equal(usable().presentationId, "0123456789abcdef");
  for (const bad of [undefined, null, "", "xyz", "0123456789ABCDEF", "0123456789abcde", "0123456789abcdef0", 12345678]) {
    assert.equal(Core.interpretTask(task({ presentation_id: bad })).presentationId, null, String(bad));
  }
  assert.equal(Core.interpretTask(task()).usable, true);   // 식별값이 없어도 열 수는 있다(다만 같은 영상으로 확인되지는 않는다)
});

test("같은 영상 판정: 식별값·대표 번호·크기가 모두 같을 때만", () => {
  const a = Core.assetIdentity(usable());
  assert.equal(Core.sameAsset(a, Core.assetIdentity(usable())), true);
  assert.equal(Core.sameAsset(a, Core.assetIdentity(usable({ presentation_id: "fedcba9876543210" }))), false);   // 영상이 바뀜
  assert.equal(Core.sameAsset(a, Core.assetIdentity(usable({ representative_slice_index: 13, display_slice_number: 14 }))), false);
  assert.equal(Core.sameAsset(a, Core.assetIdentity(usable({ image_width: 640 }))), false);
  assert.equal(Core.sameAsset(a, Core.assetIdentity(usable({ image_height: 768 }))), false);
  // 토큰만 새로 나온 경우는 같은 영상(토큰은 동일성 지표가 아니다)
  assert.equal(Core.sameAsset(a, Core.assetIdentity(usable({ problem_token: "another-token", image_url: "/other-url-same-content.png" }))), true);
});

test("같은 영상인지 확인할 수 없으면 같다고 하지 않는다(보수적)", () => {
  const known = Core.assetIdentity(usable());
  const unknown = Core.assetIdentity(Core.interpretTask(task()));   // 식별값 없음
  assert.equal(Core.sameAsset(known, unknown), false);
  assert.equal(Core.sameAsset(unknown, unknown), false);
  assert.equal(Core.sameAsset(null, known), false);
  assert.equal(Core.sameAsset(known, null), false);
  assert.equal(Core.assetIdentity({ usable: false }), null);
  assert.equal(Core.assetIdentity(null), null);
});

test("영상 변경 안내: 화면 번호만 쓰고 경로·해시·토큰을 쓰지 않는다", () => {
  const prev = Core.assetIdentity(usable());
  const next = Core.assetIdentity(usable({ representative_slice_index: 13, display_slice_number: 14, presentation_id: "fedcba9876543210" }));
  const text = Core.describeAssetChange(prev, next);
  assert.match(text, /13번째 → 14번째/);
  assert.match(text, /자동으로 옮기지 않았습니다/);
  assert.doesNotMatch(text, /fedcba|0123456789abcdef|tok|\/_images/);
  assert.match(Core.describeAssetChange(prev, prev), /확인할 수 없습니다|바뀌었/);   // 번호가 같아도 안내한다
  assert.match(Core.describeAssetChange(null, null), /자동으로 옮기지 않았습니다/);
});

test("문제 열기 실패 문구: 어떤 오류든 '다시 시도'로 이어진다", () => {
  for (const status of [0, undefined, 401, 403, 404, 409, 422, 502, 503, 500]) {
    const info = Core.describeOpenError(status, "detail");
    assert.equal(info.canRetry, true, String(status));
    assert.ok(info.message.length > 5);
  }
  assert.match(Core.describeOpenError(401).message, /로그인이 필요합니다.*다시 시도/);
  assert.match(Core.describeOpenError(0).message, /통신하지 못했습니다.*다시 시도/);
  assert.match(Core.describeOpenError(409).message, /바뀌었습니다/);
});

test("제출 오류 409·응답 유실 문구: 이미 처리됐을 수 있음을 알리고 저장 안 됐다고 단정하지 않는다", () => {
  assert.match(Core.describeHttpError(409).message, /바뀌었습니다/);
  const lost = Core.describeHttpError(0).message;
  assert.match(lost, /이미 처리됐을 수도/);
  assert.match(lost, /한 번 더 남을 수/);
  assert.doesNotMatch(lost, /저장되지 않았|저장하지 못했/);
});

test("미채점: 항상 문제 다시 열기 가능, 토큰 문제만 제출을 막는다(대표 변경 때 no_reference_for_slice 로도 다시 열 수 있다)", () => {
  const base = { ok: true, graded: false, result: null, iou: null, review_idx: null };
  for (const reason of ["no_reference_for_slice", "reference_ambiguous", "asset_changed", "study_image_unavailable", "token_invalid", "token_expired", "presentation_changed"]) {
    const v = Core.interpretGradeResponse(Object.assign({}, base, { not_graded_reason: reason }));
    assert.equal(v.canReopen, true, reason);
  }
  assert.equal(Core.interpretGradeResponse(Object.assign({}, base, { not_graded_reason: "no_reference_for_slice" })).needsReopen, false);
  assert.equal(Core.interpretGradeResponse(Object.assign({}, base, { not_graded_reason: "token_expired" })).needsReopen, true);
});

// ── 학습자 로그인 칸: 실패 문구는 상태 코드로만 고르고 서버 본문을 쓰지 않는다 ─────────────────────────────
test("로그인 실패 문구: 상태 코드별 고정 문구", () => {
  assert.equal(Core.describeLoginError(401), "아이디 또는 비밀번호가 올바르지 않습니다.");
  assert.match(Core.describeLoginError(422), /모두 입력/);
  assert.match(Core.describeLoginError(400), /모두 입력/);
  assert.match(Core.describeLoginError(429), /너무 많습니다/);
  for (const status of [0, undefined, null]) assert.match(Core.describeLoginError(status), /통신하지 못했습니다/);
  assert.match(Core.describeLoginError(500), /로그인하지 못했습니다/);
  assert.match(Core.describeLoginError(503), /로그인하지 못했습니다/);
});

test("로그인 실패 문구는 서버 본문·추가 인자를 받지 않는다(개발용 필드가 화면에 나오지 않게)", () => {
  assert.equal(Core.describeLoginError.length, 1);
  assert.equal(Core.describeLoginError(401, { plain_pw: "secret-password" }), Core.describeLoginError(401));
  assert.doesNotMatch(Core.describeLoginError(401, { plain_pw: "secret-password" }), /secret-password/);
});
