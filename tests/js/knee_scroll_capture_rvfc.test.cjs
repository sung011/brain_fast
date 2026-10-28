// 넘기며 분석 수집기 새 프레임 경로(capture_profile_v4) 시험. 실행: node tests/js/knee_scroll_capture_rvfc.test.cjs
// 가짜 시계·타이머·video(requestVideoFrameCallback)·canvas 로 '장 정답'을 알고 넣어 비교한다. 해시는 node:crypto(즉시), toBlob 은 즉시.
const assert = require("assert");
const nodeCrypto = require("node:crypto");
const path = require("path");
const K = require(path.join(__dirname, "../../assets/js/knee-scroll-capture.js"));
let n = 0;
const N = 4096;
const pattern = (o) => Uint8Array.from({ length: N }, (_, i) => 60 + Math.floor((i % 64) / 8) * 8 + Math.floor(i / 512) * 4 + o);
const refine = (sig, frac, amt) => { const a = Uint8Array.from(sig); for (let i = 0; i < Math.floor(N * frac); i++) a[i] += amt; return a; };
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function env({ rvfc = true, hashGate = false } = {}) {
  let now = 0, cur = pattern(0), timers = [], tid = 0, cb = null, cbId = 0, reason = null, vis = null, gate = null, keyVal = "k";
  const stats = { registered: 0, cancelled: 0, intervals: [], reads: 0 };
  const real = { st: global.setTimeout, ct: global.clearTimeout, si: global.setInterval, ci: global.clearInterval, now: Date.now, crypto: global.crypto };
  global.setTimeout = (f, ms) => { const id = ++tid; timers.push({ id, at: now + Math.max(0, ms || 0), f }); return id; };
  global.clearTimeout = (id) => { timers = timers.filter((t) => t.id !== id); };
  global.setInterval = (f, ms) => { const id = ++tid; stats.intervals.push(ms); timers.push({ id, at: now + ms, f, every: ms }); return id; };
  global.clearInterval = global.clearTimeout;
  Date.now = () => now;
  const digest = async (_alg, data) => { if (hashGate) await new Promise((r) => { gate = r; }); return nodeCrypto.createHash("sha256").update(Buffer.from(data.buffer || data)).digest().buffer; };
  Object.defineProperty(global, "crypto", { value: { subtle: { digest } }, configurable: true });
  const data = () => { stats.reads++; const d = new Uint8ClampedArray(N * 4); for (let i = 0; i < N; i++) { d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = cur[i]; d[i * 4 + 3] = 255; } return { data: d }; };
  const ctx = () => ({ drawImage() {}, getImageData: data });
  global.document = { visibilityState: "visible", createElement: () => ({ width: 64, height: 64, getContext: ctx, toBlob: (f) => f(new Blob([cur])) }),
    addEventListener: (_n, f) => { vis = f; }, removeEventListener: () => { vis = null; } };
  URL.createObjectURL = () => "blob:t"; URL.revokeObjectURL = () => {};
  const track = { readyState: "live" };
  const video = { srcObject: { getVideoTracks: () => [track] } };
  if (rvfc) {
    video.requestVideoFrameCallback = (f) => { stats.registered++; cb = f; return ++cbId; };
    video.cancelVideoFrameCallback = (id) => { stats.cancelled++; if (id === cbId) cb = null; };
  }
  const c = K.createCollector({ getKey: () => keyVal, getCanvas: () => ({ width: 64, height: 64, getContext: ctx }), getVideo: () => video, onChange() {}, onDone: (r) => { reason = r; } });
  async function advance(to) {   // 가짜 시계를 to 까지: 기한이 된 타이머를 시각 순서대로 실행
    for (;;) {
      timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const t = timers[0];
      if (!t || t.at > to) break;
      now = t.at;
      if (t.every) t.at += t.every; else timers.shift();
      t.f(); await flush();
    }
    now = to; await flush();
  }
  return {
    c, video, track, stats, get reason() { return reason; }, get now() { return now; },
    set(sig) { cur = sig; }, setKey(k) { keyVal = k; }, get pendingTimers() { return timers.length; },
    async frame(at, sig) { await advance(at); if (sig) cur = sig; const f = cb; cb = null; if (f) { f(at, { presentedFrames: 1, mediaTime: at / 1000 }); await flush(); } },
    advance, release() { if (gate) { const g = gate; gate = null; g(); } },
    setVisible(v, at) { now = at; document.visibilityState = v ? "visible" : "hidden"; if (vis) vis(); },
    restore() { global.setTimeout = real.st; global.clearTimeout = real.ct; global.setInterval = real.si; global.clearInterval = real.ci; Date.now = real.now; Object.defineProperty(global, "crypto", { value: real.crypto, configurable: true }); },
  };
}
const t = async (name, fn) => { await fn(); n += 1; console.log("OK", name); };

(async () => {
  await t("순수: 시간 기준 안정 확인 — 100ms 전 같은 그림은 확인 아님·대기 시각 안 늘림, 100ms 뒤 새 읽기면 채택", () => {
    const a = pattern(0), b = pattern(10);
    let r = K.confirmStep(null, b, a, 1000); assert.strictEqual(r.action, "pending");
    const p = r.pend;
    r = K.confirmStep(p, b, a, 1000); assert.deepStrictEqual([r.action, r.pend.at], ["pending", 1000]);   // 같은 순간 두 번(같은 콜백·메타데이터) = 확인 아님
    r = K.confirmStep(p, b, a, 1060); assert.deepStrictEqual([r.action, r.pend.at], ["pending", 1000]);
    r = K.confirmStep(p, b, a, 1100); assert.strictEqual(r.action, "accept");
    r = K.confirmStep(p, pattern(20), a, 1100); assert.deepStrictEqual([r.action, r.pend.at], ["pending", 1100]);   // 그림이 또 바뀜 = 새 후보
    assert.strictEqual(K.confirmStep(null, a, a, 0).action, "same");
    assert.strictEqual(K.PROFILE.id, "capture_profile_v4");
  });

  await t("새 프레임 경로: 10fps·1초 간격 15장 → 정확히 0~14 순서, merged 0, interval 타이머 없음(감시 250ms 만)", async () => {
    const e = env(); e.c.start(); assert.strictEqual(e.c.path, "rvfc");
    for (let k = 0; k < 15; k++) for (let f = 0; f < 10; f++) await e.frame(k * 1000 + f * 100 + 5, pattern(k * 3));
    await e.advance(15200);
    assert.strictEqual(e.c.frames.length, 15); assert.strictEqual(e.c.merged, 0);
    assert.deepStrictEqual(e.stats.intervals, [250]);
    assert.strictEqual(e.c.manifest("full_slice", "sagittal").capture_profile_id, "capture_profile_v4");
    e.c.clear(); e.restore();
  });

  await t("한 프레임만 오고 멈춘 화면도 확인 타이머로 채택(다음 콜백 무한 대기 없음)", async () => {
    const e = env(); e.c.start();
    for (let k = 0; k < 15; k++) await e.frame(k * 1000 + 300, pattern(k * 3));   // 장마다 콜백 1번
    await e.advance(15000);
    assert.strictEqual(e.c.frames.length, 15);
    e.c.clear(); e.restore();
  });

  await t("가변 fps(30~400ms 간격)·전환 중간 그림(50ms 두 장)은 버리고 멈춘 장만 → 15장", async () => {
    const e = env(); e.c.start();
    const gaps = [30, 400, 70, 250, 120, 380, 45, 200];
    let tm = 10;
    for (let k = 0; k < 15; k++) {
      const s = pattern(k * 3);
      if (k) { await e.frame(tm, Uint8Array.from(s, (v, i) => (v + pattern((k - 1) * 3)[i]) >> 1)); tm += 50; await e.frame(tm, refine(s, 0.5, 9)); tm += 50; }
      let g = 0; while (tm < k * 1000 + 950) { await e.frame(tm, s); tm += gaps[g++ % gaps.length]; }
      tm = (k + 1) * 1000 + 10;
    }
    await e.advance(16000);
    assert.strictEqual(e.c.frames.length, 15); assert.strictEqual(e.c.merged, 0);
    e.c.clear(); e.restore();
  });

  await t("미세하게 다른 장(평균 1/255)도 1초 간격이면 새 장 · 다듬어짐(+250·+600ms)은 합치기(14번 넘김 → 15장, merged 28)", async () => {
    let e = env(); e.c.start();
    for (let k = 0; k < 15; k++) await e.frame(k * 1000 + 100, Uint8Array.from(pattern(0), (v) => v + k));
    await e.advance(15500);
    assert.strictEqual(e.c.frames.length, 15); e.c.clear(); e.restore();
    e = env(); e.c.start();
    await e.frame(100, pattern(0));
    for (let k = 1; k <= 14; k++) { const sl = pattern(k * 5), t0 = k * 1000; await e.frame(t0, sl); await e.frame(t0 + 250, refine(sl, 0.10, 12)); await e.frame(t0 + 600, refine(sl, 0.10, 6)); }
    await e.advance(15500);
    assert.deepStrictEqual([e.c.frames.length, e.c.merged], [15, 28]); e.c.clear(); e.restore();
  });

  await t("숨김 20초 → 무변화 정지 없음·경고·복귀 뒤 콜백 없이도 이어서 채택", async () => {
    const e = env(); e.c.start();
    await e.frame(100, pattern(0)); await e.advance(400);
    e.setVisible(false, 1000); await e.advance(21000); e.setVisible(true, 21000);
    e.set(pattern(10)); await e.advance(21500);
    assert.deepStrictEqual([e.c.frames.length, e.c.hiddenSeen, e.c.running, e.reason], [2, true, true, null]);
    e.c.clear(); e.restore();
  });

  await t("콜백이 전혀 오지 않으면 감시가 무변화 15초로 멈춤 · 5초 무추가 안내(stalled) 뒤 새 장이면 해제", async () => {
    let e = env(); e.c.start();
    await e.advance(400); assert.strictEqual(e.c.frames.length, 1);   // 첫 장은 시작 확인으로
    await e.advance(5500); assert.deepStrictEqual([e.c.stalled, e.c.noticeCount], [true, 1]);
    await e.frame(6000, pattern(9)); await e.advance(6300);
    assert.deepStrictEqual([e.c.frames.length, e.c.stalled], [2, false]);
    await e.advance(22000); assert.deepStrictEqual([e.c.running, e.reason], [false, "idle"]);
    e.restore();
  });

  await t("공유 트랙 종료 → 감시가 stop('ended'), 콜백 취소", async () => {
    const e = env(); e.c.start(); await e.frame(100, pattern(0)); await e.advance(300);
    e.track.readyState = "ended"; await e.advance(700);
    assert.deepStrictEqual([e.c.running, e.reason], [false, "ended"]); assert.ok(e.stats.cancelled >= 1);
    e.restore();
  });

  await t("확인(해시) 중에 온 새 장은 끝난 직후 다시 확인해 채택(추가 콜백 없이)", async () => {
    const e = env({ hashGate: true }); e.c.start();
    await e.advance(150);                      // 첫 장 채택 → 해시 대기
    await e.frame(300, pattern(12));           // 바쁠 때 새 장 프레임 1개
    e.release(); await e.advance(320);          // 첫 장 해시 끝 → 즉시 다시 확인 → 후보
    await e.advance(450); e.release(); await e.advance(500);
    assert.strictEqual(e.c.frames.length, 2);
    e.c.clear(); e.restore();
  });

  await t("세대: stop/clear/restart 뒤 늦은 콜백·확인 결과는 버림, 경로 중복 없음", async () => {
    const e = env({ hashGate: true }); e.c.start();
    await e.advance(150);                       // 첫 장 해시 대기 중
    e.c.stop("user"); e.release(); await e.advance(400);
    assert.deepStrictEqual([e.c.frames.length, e.c.running], [0, false]);
    assert.ok(e.stats.cancelled >= 1);
    e.c.start(); e.c.start();                   // 연속 시작해도 콜백 등록은 지금 세대 하나
    await e.frame(600, pattern(3));
    const before = e.stats.registered;
    await e.frame(700, pattern(3));
    assert.strictEqual(e.stats.registered - before, 1);
    e.release(); await e.advance(900); e.release(); await e.advance(1000);
    assert.ok(e.c.frames.length >= 1 && e.c.frames.length <= 2);
    assert.deepStrictEqual(e.stats.intervals.filter((ms) => ms === 100), []);
    e.c.clear(); e.restore();
  });

  await t("rVFC 미지원 → interval 경로(v3 판정, 100ms), 프로필 _interval · 15장", async () => {
    const e = env({ rvfc: false }); e.c.start(); assert.strictEqual(e.c.path, "interval");
    for (let k = 0; k < 15; k++) { e.set(pattern(k * 3)); await e.advance(k * 1000 + 900); }
    assert.strictEqual(e.c.frames.length, 15); assert.deepStrictEqual(e.stats.intervals, [100]);
    assert.strictEqual(e.c.manifest("full_slice", "sagittal").capture_profile_id, "capture_profile_v4_interval");
    e.c.clear(); e.restore();
  });

  // ── CAPFIX-PREP-001: 숨김·처리 대기(busy) 중에도 종료 조건(전체 시간·공유 종료·열쇠 변경)을 처리 ──
  const stopped = (e, why) => { assert.deepStrictEqual([e.c.running, e.reason], [false, why]); assert.strictEqual(e.pendingTimers, 0, "타이머·감시가 남음"); };

  await t("새 프레임 경로: 1초에 숨김 → 타이머가 도는 채 121초 → timeout·running false·콜백/타이머 취소", async () => {
    const e = env(); e.c.start(); await e.frame(100, pattern(0)); await e.advance(300);
    e.setVisible(false, 1000); await e.advance(121000);
    stopped(e, "timeout"); assert.ok(e.stats.cancelled >= 1);
    e.restore();
  });

  await t("새 프레임 경로: 숨김 중 공유 트랙 종료 → ended / 숨김 중 박스 열쇠 변경 → reset", async () => {
    let e = env(); e.c.start(); await e.frame(100, pattern(0)); await e.advance(300);
    e.setVisible(false, 1000); await e.advance(3000); e.track.readyState = "ended"; await e.advance(3300);
    stopped(e, "ended"); e.restore();
    e = env(); e.c.start(); await e.frame(100, pattern(0)); await e.advance(300);
    e.setVisible(false, 1000); await e.advance(3000); e.setKey("k2"); await e.advance(3300);
    stopped(e, "reset"); e.restore();
  });

  await t("새 프레임 경로: 숨김 30초(감시는 계속 돎)만으로는 idle 아님 → 복귀 뒤 이어서 채택", async () => {
    const e = env(); e.c.start(); await e.frame(100, pattern(0)); await e.advance(300);
    e.setVisible(false, 1000); await e.advance(31000); e.setVisible(true, 31000);
    assert.deepStrictEqual([e.c.running, e.reason], [true, null]);
    await e.frame(31200, pattern(9)); await e.advance(31500);
    assert.strictEqual(e.c.frames.length, 2);
    e.c.clear(); e.restore();
  });

  await t("새 프레임 경로: 해시 대기(busy) 중 열쇠 변경 → 감시가 reset, 늦게 끝난 해시는 장을 더하지 않음", async () => {
    const e = env({ hashGate: true }); e.c.start();
    await e.advance(150);                       // 첫 장 해시 대기
    e.setKey("k2"); await e.advance(400);
    stopped(e, "reset");
    e.release(); await e.advance(600);
    assert.strictEqual(e.c.frames.length, 0);
    e.restore();
  });

  await t("interval 경로: 해시 대기(busy) 중 121초 → timeout · busy 중 열쇠 변경 → reset · 늦은 결과 버림", async () => {
    let e = env({ rvfc: false, hashGate: true }); e.c.start();
    await e.advance(250);                       // 두 번째 확인에서 채택 → 해시 대기
    await e.advance(121000);
    stopped(e, "timeout"); e.release(); await e.advance(121200);
    assert.strictEqual(e.c.frames.length, 0); e.restore();
    e = env({ rvfc: false, hashGate: true }); e.c.start();
    await e.advance(250); e.setKey("k2"); await e.advance(400);
    stopped(e, "reset"); e.release(); await e.advance(600);
    assert.strictEqual(e.c.frames.length, 0); e.restore();
  });

  await t("interval 경로: 숨김 중 121초 → timeout, 숨김 중 트랙 종료 → ended, 숨김 30초만으로는 idle 아님", async () => {
    let e = env({ rvfc: false }); e.c.start(); await e.advance(300);
    e.setVisible(false, 1000); await e.advance(121000);
    stopped(e, "timeout"); e.restore();
    e = env({ rvfc: false }); e.c.start(); await e.advance(300);
    e.setVisible(false, 1000); await e.advance(2000); e.track.readyState = "ended"; await e.advance(2200);
    stopped(e, "ended"); e.restore();
    e = env({ rvfc: false }); e.c.start(); await e.advance(300);
    e.setVisible(false, 1000); await e.advance(31000); e.setVisible(true, 31000);
    e.set(pattern(9)); await e.advance(31500);
    assert.deepStrictEqual([e.c.running, e.c.frames.length], [true, 2]);
    e.c.clear(); e.restore();
  });

  console.log(`\n${n}개 통과`);
})().catch((err) => { console.error(err); process.exit(1); });
