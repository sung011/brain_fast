/*
 * 무릎 '넘기며 분석' 수집기 (MSN-20261021-001 v2 설계 4장, capture_profile_v3)
 * 화면 공유 영상에서 고정한 박스(ROI)를 100ms 마다 확인해, 사용자가 MRI 를 넘기는 동안 바뀐 장을 자동으로 모은다.
 * - 변화 후보: 64×64 회색 축소본의 평균 절대 차이/255 ≥ 0.012 또는 8/255 이상 차이 나는 화소 비율 ≥ 0.02
 * - 미세 변화: 위 기준엔 못 미쳐도 평균 차이 ≥ 0.002(약 0.5/255)면 '비슷한 다른 장' 후보로 보고 화소 해시로 판단(설계 4장 3번).
 *   그보다 작은 차이(몇 화소 1단계 노이즈 등)는 같은 화면으로 본다 — 허용 한계는 tests/js/knee_scroll_capture.test.cjs 에 고정.
 * - 안정 확인: 후보가 바로 앞 확인과 평균 차이 ≤ 0.003 인 상태가 두 번 이어질 때 채택(넘기는 중간 그림 제외)
 * - 정확 중복 제거: 축소·인코딩 **전** 원 ROI 회색 화소의 SHA-256. 비슷하지만 화소가 다른 장은 버리지 않는다.
 * - 화면 다듬어짐 합치기(v3, 실제 화면 공유 재현 2026-10-22 + Codex CAPTURE-V2-001): 실제 캡처는 장을 넘긴 뒤 약 0.3초에 새 장(평균 차이 0.015~0.030)이 오고
 *   약 0.55·0.9초에 **같은 장**이 단계적으로 다듬어진다(그 장 대비 0.002~0.006 = 큰 변화의 21~27%). 그래서
 *   '기준 장'(수집 첫 장, 또는 직전 채택 장과 madNew 이상 차이 난 큰 변화 장)이 채택된 시각부터 **고정** settleMs(0.7초) 안에 오고,
 *   직전 장과 madNew 미만 차이이며 기준 장 대비 차이가 기준 변화 크기의 settleRatio(40%) 이하인 후보만 마지막 장과 바꿔 끼운다.
 *   교체는 기준 시각을 늘리지 않는다(연장 없음). 큰 변화 없이 이어지는 작은 변화(비슷한 다른 장, NOTE2)는 기준 장이 없어 모두 새 장으로 보존한다.
 *   한계: 큰 변화 직후 0.7초 안에 온 아주 비슷한 다른 장, 수집 시작 0.7초 안의 아주 작은 변화는 같은 장으로 합쳐질 수 있다 → merged 횟수로 화면에 알린다.
 * - 분석 화면이 가려지거나 최소화되면(document hidden) 브라우저가 타이머·영상 갱신을 크게 늦추거나 멈춘다(CAPTURE-V2-003).
 *   visibilitychange 로 숨김 시작·복귀를 바로 기록하고, 확인 간격이 suspendGapMs(2초)보다 벌어진 것도 숨김/정지로 본다.
 *   숨김·정지 시간은 무변화(idle) 시간에서 빼고 hiddenSeen 으로 '창을 나란히 두라' 안내를 띄운다. 가려진 동안 지나간 장은 복구되지 않는다.
 *   전체 120초 제한은 실제 경과 시간(숨김 포함) 그대로다.
 * - stop/clear/restart 는 세대를 올려, 이미 진행 중인 해시·인코딩 결과가 정지된 목록이나 새 수집에 섞이지 않게 한다.
 * - 최소 10 · 권장 15 · 최대 20(도달하면 자동 분석). 전체 120초 · 무변화 15초면 수집을 멈춘다.
 * - 모델 입력 PNG: 최대 변 512px(확대 없음, 종횡비 유지), 모든 장 같은 크기. 인코딩은 한 번에 하나, 보관 Blob 최대 20.
 * 브라우저 전용 부분(canvas·crypto.subtle)과 순수 계산(diff·stable·결정)을 나눠, 순수 계산은 node 로 시험한다.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.KneeScrollCapture = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const PROFILE = {
    id: "capture_profile_v3", min: 10, recommend: 15, max: 20, intervalMs: 100, totalMs: 120000, idleMs: 15000, settleMs: 700, settleRatio: 0.4, suspendGapMs: 2000,
    sig: 64, madNew: 0.012, pixDiff: 8 / 255, pixFrac: 0.02, subtleMad: 0.002, stableMad: 0.003, stableRuns: 2, maxSide: 512,
  };

  /** 두 64×64 회색 서명(0~255 Uint8)의 평균 절대 차이(0~1)와 큰 차이 화소 비율. */
  function sigDiff(a, b) {
    if (!a || !b || a.length !== b.length) return { mad: 1, frac: 1 };
    let sum = 0, big = 0;
    const thr = PROFILE.pixDiff * 255;
    for (let i = 0; i < a.length; i++) {
      const d = Math.abs(a[i] - b[i]);
      sum += d;
      if (d >= thr) big++;
    }
    return { mad: sum / a.length / 255, frac: big / a.length };
  }

  function isNewCandidate(sig, lastAccepted) {
    if (!lastAccepted) return true;
    const d = sigDiff(sig, lastAccepted);
    return d.mad >= PROFILE.madNew || d.frac >= PROFILE.pixFrac || d.mad >= PROFILE.subtleMad;
  }

  /**
   * 한 번의 확인(tick) 결정. state: {lastAccepted, prevSig, stableRuns}. 반환 {accept, state}.
   * 새 후보이면서 직전 확인과 거의 같은 상태가 stableRuns 번 이어질 때만 accept.
   */
  function step(state, sig) {
    const s = Object.assign({ lastAccepted: null, prevSig: null, stableRuns: 0 }, state || {});
    const candidate = isNewCandidate(sig, s.lastAccepted);
    const stableWithPrev = s.prevSig ? sigDiff(sig, s.prevSig).mad <= PROFILE.stableMad : false;
    s.stableRuns = candidate && stableWithPrev ? s.stableRuns + 1 : 0;
    s.prevSig = sig;
    if (candidate && s.stableRuns >= PROFILE.stableRuns - 1 && stableWithPrev) {
      s.lastAccepted = sig;
      s.stableRuns = 0;
      return { accept: true, state: s };
    }
    return { accept: false, state: s };
  }

  /**
   * 채택된 후보를 새 장으로 더할지, 기준 장의 다듬어진 그림으로 마지막 장과 바꿔 끼울지 결정(순수 계산, 시험 대상).
   * anchor: {at, sig, transMad} | null — 기준 장(수집 첫 장 또는 큰 변화 장). 반환 {replace, anchor(다음 기준)}.
   */
  function settleDecision(anchor, sig, prevAccepted, now) {
    const dPrev = prevAccepted ? sigDiff(sig, prevAccepted) : null;
    const replace = !!(anchor && prevAccepted && now - anchor.at <= PROFILE.settleMs && dPrev.mad < PROFILE.madNew
      && sigDiff(sig, anchor.sig).mad <= PROFILE.settleRatio * anchor.transMad);
    if (replace) return { replace: true, anchor };   // 기준 시각은 그대로(교체로 연장하지 않음)
    const next = !prevAccepted ? { at: now, sig, transMad: PROFILE.madNew }
      : dPrev.mad >= PROFILE.madNew ? { at: now, sig, transMad: dPrev.mad } : null;
    return { replace: false, anchor: next };
  }

  /** 수집 상태 문구용: 장 수 → 단계. */
  function phase(count) {
    if (count >= PROFILE.max) return "max";
    if (count >= PROFILE.recommend) return "recommended";
    if (count >= PROFILE.min) return "enough";
    return "collecting";
  }

  function targetSize(w, h) {
    const k = Math.min(1, PROFILE.maxSide / Math.max(w, h));
    return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)) };
  }

  // ── 브라우저 전용 ───────────────────────────────────────────────
  function signature(canvas) {
    const c = document.createElement("canvas");
    c.width = c.height = PROFILE.sig;
    const x = c.getContext("2d", { willReadFrequently: true });
    x.drawImage(canvas, 0, 0, PROFILE.sig, PROFILE.sig);
    const d = x.getImageData(0, 0, PROFILE.sig, PROFILE.sig).data;
    const out = new Uint8Array(PROFILE.sig * PROFILE.sig);
    for (let i = 0, j = 0; i < d.length; i += 4, j++) out[j] = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    return out;
  }

  async function exactHash(canvas) {
    const x = canvas.getContext("2d", { willReadFrequently: true });
    const d = x.getImageData(0, 0, canvas.width, canvas.height).data;
    const g = new Uint8Array(canvas.width * canvas.height + 8);
    new DataView(g.buffer).setUint32(0, canvas.width);
    new DataView(g.buffer).setUint32(4, canvas.height);
    for (let i = 0, j = 8; i < d.length; i += 4, j++) g[j] = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    const h = await crypto.subtle.digest("SHA-256", g);
    return Array.from(new Uint8Array(h)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  function encode(canvas, size) {
    const c = document.createElement("canvas");
    c.width = size.w;
    c.height = size.h;
    c.getContext("2d").drawImage(canvas, 0, 0, size.w, size.h);
    return new Promise((resolve) => c.toBlob(resolve, "image/png"));
  }

  /**
   * 수집기. opts: { getCanvas():canvas|null, getKey():string|null, onChange(info), onDone(reason) }
   * getKey 는 ROI 원본 좌표·영상 크기·촬영면·범위를 묶은 문자열. 바뀌면 수집을 버린다(onDone('reset')).
   */
  function createCollector(opts) {
    let timer = null, busy = null, st = null, frames = [], hashes = new Set(), key = null, size = null, started = 0, lastChange = 0, seq = 0, gen = 0;
    let anchor = null, merged = 0, hiddenSeen = false, hiddenAt = null, lastTickAt = 0;
    const exclude = (ms, now) => { lastChange = Math.min(now, lastChange + Math.max(0, ms)); };   // 숨김·정지 시간은 무변화로 세지 않음
    const markHidden = () => { if (!hiddenSeen) { hiddenSeen = true; opts.onChange && opts.onChange(info({ hidden: true })); } };
    function onVisibility() {
      if (!timer) return;
      const now = Date.now();
      if (document.visibilityState === "hidden") { hiddenAt = now; markHidden(); return; }
      if (hiddenAt !== null) { exclude(now - hiddenAt, now); hiddenAt = null; }
      if (st) st.prevSig = null;   // 복귀 뒤 안정 확인을 처음부터
      lastTickAt = now;
      opts.onChange && opts.onChange(info({ resumed: true }));
    }
    const hasDocEvents = () => typeof document !== "undefined" && typeof document.addEventListener === "function";
    const info = (extra) => Object.assign({ count: frames.length, phase: phase(frames.length), frames, profile: PROFILE }, extra || {});

    function stop(reason) {
      if (hasDocEvents() && typeof document.removeEventListener === "function") document.removeEventListener("visibilitychange", onVisibility);
      if (timer) clearInterval(timer);
      timer = null;
      gen++;          // 진행 중이던 해시·인코딩 결과가 정지된 목록에 더해지지 않게
      busy = null;
      opts.onDone && opts.onDone(reason, info());
    }

    async function tick(myGen) {
      if (myGen !== gen) return;
      const now = Date.now();
      const gap = now - lastTickAt;
      lastTickAt = now;
      if (gap > PROFILE.suspendGapMs) {   // 브라우저가 타이머를 멈췄던 구간(가려짐·최소화 등) → 숨김으로 기록, 무변화 시간에서 뺌
        markHidden();
        exclude(gap, now);
        if (st) st.prevSig = null;
      }
      if (busy) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {   // 가려진 동안 확인이 와도 수집하지 않음
        markHidden();
        exclude(gap, now);
        return;
      }
      if (opts.getKey() !== key) return stop("reset");
      if (now - started > PROFILE.totalMs) return stop("timeout");
      if (now - lastChange > PROFILE.idleMs) return stop("idle");
      const canvas = opts.getCanvas();
      if (!canvas) return;
      const token = {};
      busy = token;
      try {
        const sig = signature(canvas);
        const prevAccepted = st ? st.lastAccepted : null;
        const r = step(st, sig);
        st = r.state;
        if (!r.accept) return;
        lastChange = now;
        const decision = settleDecision(frames.length ? anchor : null, sig, frames.length ? prevAccepted : null, now);
        const h = await exactHash(canvas);
        if (myGen !== gen) return;
        if (hashes.has(h)) { opts.onChange && opts.onChange(info({ duplicate: true })); return; }
        if (!size) size = targetSize(canvas.width, canvas.height);
        if (frames.length && (canvas.width !== frames[0].srcW || canvas.height !== frames[0].srcH)) return stop("reset");   // 박스/해상도 바뀜
        const blob = await encode(canvas, size);
        if (myGen !== gen || !blob) return;
        hashes.add(h);
        const frame = { sequence: seq++, capturedAt: now - started, blob, url: URL.createObjectURL(blob), srcW: canvas.width, srcH: canvas.height, hash: h };
        if (decision.replace) {   // 기준 장이 다듬어진 그림 → 마지막 장을 바꿔 끼움(장 수 그대로, 합친 횟수 기록)
          const old = frames.pop();
          URL.revokeObjectURL(old.url);
          hashes.delete(old.hash);
          merged++;
        }
        anchor = decision.anchor;
        frames.push(frame);
        opts.onChange && opts.onChange(info());
        if (frames.length >= PROFILE.max) stop("max");
      } finally {
        if (busy === token) busy = null;   // 오래된 세대의 finally 가 새 세대 진행 표시를 지우지 않게
      }
    }

    return {
      start() {
        this.clear();
        gen++;
        key = opts.getKey();
        started = lastChange = lastTickAt = Date.now();
        if (hasDocEvents()) document.addEventListener("visibilitychange", onVisibility);
        const myGen = gen;
        timer = setInterval(() => tick(myGen), PROFILE.intervalMs);
        opts.onChange && opts.onChange(info());
      },
      stop(reason) { if (timer) stop(reason || "user"); },
      clear() {
        if (hasDocEvents() && typeof document.removeEventListener === "function") document.removeEventListener("visibilitychange", onVisibility);
        if (timer) clearInterval(timer);
        timer = null;
        gen++;
        frames.forEach((f) => URL.revokeObjectURL(f.url));
        frames = []; hashes = new Set(); st = null; size = null; seq = 0; key = null; busy = null; anchor = null; merged = 0; hiddenSeen = false; hiddenAt = null;
      },
      get running() { return !!timer; },
      get hiddenSeen() { return hiddenSeen; },
      get merged() { return merged; },
      get frames() { return frames; },
      manifest(scope, plane) {
        return {
          schema_version: 1, plane, image_scope: scope, capture_intent: "scroll", capture_profile_id: PROFILE.id,
          frames: frames.map((f, i) => ({ sequence: f.sequence, file_index: i, captured_at_ms: Math.max(0, Math.round(f.capturedAt)) })),
        };
      },
    };
  }

  return { PROFILE, sigDiff, isNewCandidate, settleDecision, step, phase, targetSize, createCollector };
});
