/*
 * 무릎 '넘기며 분석' 수집기 (MSN-20261021-001, capture_profile_v4 — 수집 신뢰성 수정 spec capture-reliability-20261028-r1)
 * v3(제품 7ddd3e9f…)과 같은 변화·미세 변화·정확 중복·다듬어짐 합치기·숨김·10/15/20·120초·무변화 15초 규칙을 그대로 쓰고, 확인 시점만 바꾼다.
 * - F-A 새 프레임 구동: 브라우저가 video.requestVideoFrameCallback 을 지원하면 공유 영상에 **새 프레임이 올 때마다** 박스를 확인한다.
 *   안정 확인은 '시간 기준'(F-D): 새 후보를 처음 본 뒤 **최소 stableMinMs(100ms) 지나 다시 읽은 화소**가 그 후보와 평균 차이 ≤ stableMad(0.003)일 때 채택.
 *   같은 콜백·같은 메타데이터를 두 번 세지 않는다(확인은 항상 시간 간격이 있는 새 화소 읽기). 한 프레임만 오고 멈춘 화면도
 *   확인 타이머(후보 시각 + 100ms)가 다시 읽어 채택하므로, 다음 콜백을 무한히 기다리지 않는다.
 *   확인(해시·인코딩) 중에 온 프레임은 버리지 않고 끝난 직후 한 번 다시 확인한다.
 * - 지원하지 않으면 v3 와 같은 100ms 간격 확인(interval 경로). 두 경로를 동시에 돌리지 않는다.
 * - 감시(watchdog, 250ms): 새 프레임이 오지 않아도 박스·원해상도·촬영면 열쇠 변경, 공유 트랙 종료, 숨김·타이머 정지(2초 넘는 간격),
 *   무변화 15초·전체 120초를 확인한다. 화소는 읽지 않는다.
 * - F-E 안내: 새 장이 5초 동안 추가되지 않으면 stalled=true 로 알린다(비차단). 정지 화면일 수도 있으므로 누락 확정·자동 재시작·자동 제출은 하지 않는다.
 * - stop/clear/restart 는 세대를 올리고 콜백·확인 타이머·감시를 모두 취소한다. 다른 세대의 늦은 해시·인코딩 결과는 버린다.
 * - capture_profile_id: 새 프레임 경로 'capture_profile_v4', interval 경로 'capture_profile_v4_interval'.
 * 아래 v3 설명(변화 후보·미세 변화·정확 중복·다듬어짐 합치기·숨김)은 그대로 유효하다.
 * - 변화 후보: 64×64 회색 축소본의 평균 절대 차이/255 ≥ 0.012 또는 8/255 이상 차이 나는 화소 비율 ≥ 0.02, 미세 변화 평균 차이 ≥ 0.002
 * - 정확 중복 제거: 축소·인코딩 전 원 ROI 회색 화소의 SHA-256
 * - 다듬어짐 합치기: 기준 장 채택 시각부터 고정 settleMs(0.7초) 안, 직전 장과 madNew 미만·기준 장 대비 변화가 기준 변화의 40% 이하이면 마지막 장과 바꿔 끼움(연장 없음)
 * - 모델 입력 PNG: 최대 변 512px(확대 없음, 종횡비 유지), 모든 장 같은 크기. 인코딩은 한 번에 하나, 보관 Blob 최대 20.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.KneeScrollCapture = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const PROFILE = {
    id: "capture_profile_v4", min: 10, recommend: 15, max: 20, intervalMs: 100, totalMs: 120000, idleMs: 15000, settleMs: 700, settleRatio: 0.4, suspendGapMs: 2000,
    sig: 64, madNew: 0.012, pixDiff: 8 / 255, pixFrac: 0.02, subtleMad: 0.002, stableMad: 0.003, stableRuns: 2, maxSide: 512,
    stableMinMs: 100, watchdogMs: 250, noticeMs: 5000,
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
   * (interval 경로, v3 그대로) 한 번의 확인(tick) 결정. state: {lastAccepted, prevSig, stableRuns}. 반환 {accept, state}.
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
   * (새 프레임 경로) 시간 기준 안정 확인 — 순수 계산. pend: {sig, at} | null(확인 대기 중인 후보), lastAccepted: 직전 채택 서명.
   * 반환 {action, pend}: action = 'same'(새 후보 아님) | 'pending'(후보 대기·확인 시각 pend.at+stableMinMs) | 'accept'.
   * 같은 그림이라도 최소 간격 전에 읽은 것은 확인으로 세지 않고, 대기 시각(pend.at)도 늘리지 않는다.
   */
  function confirmStep(pend, sig, lastAccepted, now) {
    if (!isNewCandidate(sig, lastAccepted)) return { action: "same", pend: null };
    if (pend && sigDiff(sig, pend.sig).mad <= PROFILE.stableMad) {
      if (now - pend.at >= PROFILE.stableMinMs) return { action: "accept", pend: null };
      return { action: "pending", pend };
    }
    return { action: "pending", pend: { sig, at: now } };
  }

  /**
   * 채택된 후보를 새 장으로 더할지, 기준 장의 다듬어진 그림으로 마지막 장과 바꿔 끼울지 결정(v3 그대로, 순수 계산).
   */
  function settleDecision(anchor, sig, prevAccepted, now) {
    const dPrev = prevAccepted ? sigDiff(sig, prevAccepted) : null;
    const replace = !!(anchor && prevAccepted && now - anchor.at <= PROFILE.settleMs && dPrev.mad < PROFILE.madNew
      && sigDiff(sig, anchor.sig).mad <= PROFILE.settleRatio * anchor.transMad);
    if (replace) return { replace: true, anchor };
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
   * 수집기. opts: { getCanvas():canvas|null, getKey():string|null, getVideo?():HTMLVideoElement|null, onChange(info), onDone(reason) }
   * getVideo 가 rVFC 를 지원하는 video 를 주면 새 프레임 경로, 아니면 interval 경로.
   */
  function createCollector(opts) {
    let timer = null, watchdog = null, confirmTimer = null, rvfcHandle = null, rvfcVideo = null, path = null;
    let busy = null, st = null, pend = null, recheck = false, frames = [], hashes = new Set(), key = null, size = null, started = 0, lastChange = 0, seq = 0, gen = 0;
    let anchor = null, merged = 0, hiddenSeen = false, hiddenAt = null, lastTickAt = 0, lastAddedAt = 0, stalled = false, noticeCount = 0;
    const exclude = (ms, now) => { lastChange = Math.min(now, lastChange + Math.max(0, ms)); };
    const markHidden = () => { if (!hiddenSeen) { hiddenSeen = true; opts.onChange && opts.onChange(info({ hidden: true })); } };
    const running = () => !!(timer || watchdog);
    function onVisibility() {
      if (!running()) return;
      const now = Date.now();
      if (document.visibilityState === "hidden") { hiddenAt = now; markHidden(); return; }
      if (hiddenAt !== null) { exclude(now - hiddenAt, now); lastAddedAt += now - hiddenAt; hiddenAt = null; }
      if (st) st.prevSig = null;
      pend = null;
      lastTickAt = now;
      if (path === "rvfc") scheduleConfirm(0);   // 복귀 뒤 새 프레임이 없어도 한 번 확인
      opts.onChange && opts.onChange(info({ resumed: true }));
    }
    const hasDocEvents = () => typeof document !== "undefined" && typeof document.addEventListener === "function";
    const info = (extra) => Object.assign({ count: frames.length, phase: phase(frames.length), frames, profile: PROFILE, path, stalled }, extra || {});

    function cancelAll() {
      if (hasDocEvents() && typeof document.removeEventListener === "function") document.removeEventListener("visibilitychange", onVisibility);
      if (timer) clearInterval(timer);
      if (watchdog) clearInterval(watchdog);
      if (confirmTimer) clearTimeout(confirmTimer);
      if (rvfcVideo && rvfcHandle !== null && typeof rvfcVideo.cancelVideoFrameCallback === "function") rvfcVideo.cancelVideoFrameCallback(rvfcHandle);
      timer = watchdog = confirmTimer = null; rvfcHandle = null; rvfcVideo = null;
      gen++;
      busy = null; recheck = false; pend = null;
    }

    function stop(reason) {
      cancelAll();
      opts.onDone && opts.onDone(reason, info());
    }

    function setStalled(v) {
      if (v === stalled) return;
      stalled = v;
      if (v) noticeCount++;
      opts.onChange && opts.onChange(info({ notice: v }));
    }

    /** 종료 조건(숨김·처리 대기와 무관하게 먼저 확인): 공유 종료·박스/원해상도/촬영면 열쇠 변경·전체 시간(숨김 포함). 멈췄으면 true. */
    function endGuards(now) {
      if (trackEnded()) { stop("ended"); return true; }
      if (opts.getKey() !== key) { stop("reset"); return true; }
      if (now - started > PROFILE.totalMs) { stop("timeout"); return true; }
      return false;
    }

    /** 무변화 정지(보이는 동안만 — 숨김·정지 시간은 exclude 로 이미 빠짐). 멈췄으면 true. */
    function idleGuard(now) {
      if (now - lastChange > PROFILE.idleMs) { stop("idle"); return true; }
      return false;
    }

    const isHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

    function trackEnded() {
      const v = opts.getVideo ? opts.getVideo() : null;
      const so = v && v.srcObject;
      const t = so && typeof so.getVideoTracks === "function" ? so.getVideoTracks()[0] : null;
      return !!(t && t.readyState === "ended");
    }

    /** 채택된 서명·캔버스를 목록에 넣는다(v3 와 같은 해시·중복·크기·인코딩·다듬어짐 합치기). */
    async function commit(myGen, canvas, sig, prevAccepted, now) {
      lastChange = now;
      const decision = settleDecision(frames.length ? anchor : null, sig, frames.length ? prevAccepted : null, now);
      const h = await exactHash(canvas);
      if (myGen !== gen) return;
      if (hashes.has(h)) { opts.onChange && opts.onChange(info({ duplicate: true })); return; }
      if (!size) size = targetSize(canvas.width, canvas.height);
      if (frames.length && (canvas.width !== frames[0].srcW || canvas.height !== frames[0].srcH)) return stop("reset");
      const blob = await encode(canvas, size);
      if (myGen !== gen || !blob) return;
      hashes.add(h);
      const frame = { sequence: seq++, capturedAt: now - started, blob, url: URL.createObjectURL(blob), srcW: canvas.width, srcH: canvas.height, hash: h };
      if (decision.replace) {
        const old = frames.pop();
        URL.revokeObjectURL(old.url);
        hashes.delete(old.hash);
        merged++;
      } else {
        lastAddedAt = Date.now();
        setStalled(false);
      }
      anchor = decision.anchor;
      frames.push(frame);
      opts.onChange && opts.onChange(info());
      if (frames.length >= PROFILE.max) stop("max");
    }

    // ── interval 경로(v3 와 같은 판정) ──
    async function tick(myGen) {
      if (myGen !== gen) return;
      const now = Date.now();
      const gap = now - lastTickAt;
      lastTickAt = now;
      if (gap > PROFILE.suspendGapMs) {
        markHidden();
        exclude(gap, now);
        lastAddedAt += gap;
        if (st) st.prevSig = null;
      }
      if (endGuards(now)) return;          // 숨김·busy 보다 먼저(CAPFIX-PREP-001)
      if (isHidden()) {
        markHidden();
        exclude(gap, now);
        return;
      }
      if (!stalled && now - lastAddedAt >= PROFILE.noticeMs) setStalled(true);
      if (busy) return;
      if (idleGuard(now)) return;
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
        await commit(myGen, canvas, sig, prevAccepted, now);
      } finally {
        if (busy === token) busy = null;
      }
    }

    // ── 새 프레임 경로(F-A + 시간 기준 안정 확인) ──
    function scheduleConfirm(delay) {
      if (confirmTimer) clearTimeout(confirmTimer);
      const myGen = gen;
      confirmTimer = setTimeout(() => { confirmTimer = null; check(myGen, "confirm"); }, Math.max(0, delay));
    }

    async function check(myGen, why) {
      if (myGen !== gen) return;
      if (busy) { recheck = true; return; }
      const now = Date.now();
      if (endGuards(now)) return;
      if (isHidden()) { markHidden(); return; }
      if (idleGuard(now)) return;
      const canvas = opts.getCanvas();
      if (!canvas) return;
      const token = {};
      busy = token;
      try {
        const sig = signature(canvas);
        const prevAccepted = st ? st.lastAccepted : null;
        const r = confirmStep(pend, sig, prevAccepted, now);
        pend = r.pend;
        if (r.action === "pending") { scheduleConfirm(pend.at + PROFILE.stableMinMs - now); return; }
        if (r.action !== "accept") return;
        st = { lastAccepted: sig };
        await commit(myGen, canvas, sig, prevAccepted, now);
      } finally {
        if (busy === token) {
          busy = null;
          if (recheck && myGen === gen) { recheck = false; scheduleConfirm(0); }   // 확인 중 온 프레임 → 끝난 직후 다시 확인
        }
      }
    }

    function onFrame(myGen) {
      return () => {
        if (myGen !== gen || !rvfcVideo) return;
        rvfcHandle = rvfcVideo.requestVideoFrameCallback(onFrame(myGen));   // 다음 프레임 먼저 등록(이번 확인이 길어도 놓치지 않게)
        check(myGen, "frame");
      };
    }

    function watch(myGen) {
      if (myGen !== gen) return;
      const now = Date.now();
      const gap = now - lastTickAt;
      lastTickAt = now;
      if (gap > PROFILE.suspendGapMs) {   // 감시 타이머가 멈췄던 구간 = 가려짐·최소화 등
        markHidden();
        exclude(gap, now);
        lastAddedAt += gap;
        pend = null;
      }
      const hidden = isHidden();
      if (hidden) {
        markHidden();
        if (gap <= PROFILE.suspendGapMs) { exclude(gap, now); lastAddedAt += gap; }
      }
      if (endGuards(now)) return;          // 숨김 중에도 공유 종료·열쇠 변경·전체 시간은 처리(CAPFIX-PREP-001)
      if (hidden) return;                  // 무변화 정지·안내는 보이는 동안만
      if (idleGuard(now)) return;
      if (!stalled && now - lastAddedAt >= PROFILE.noticeMs) setStalled(true);
    }

    return {
      start() {
        this.clear();
        gen++;
        key = opts.getKey();
        started = lastChange = lastTickAt = lastAddedAt = Date.now();
        if (hasDocEvents()) document.addEventListener("visibilitychange", onVisibility);
        const myGen = gen;
        const v = opts.getVideo ? opts.getVideo() : null;
        if (v && typeof v.requestVideoFrameCallback === "function") {
          path = "rvfc";
          rvfcVideo = v;
          rvfcHandle = v.requestVideoFrameCallback(onFrame(myGen));
          watchdog = setInterval(() => watch(myGen), PROFILE.watchdogMs);
          scheduleConfirm(0);   // 첫 장: 새 프레임을 기다리지 않고 지금 화면부터 확인
        } else {
          path = "interval";
          timer = setInterval(() => tick(myGen), PROFILE.intervalMs);
        }
        opts.onChange && opts.onChange(info());
      },
      stop(reason) { if (running()) stop(reason || "user"); },
      clear() {
        cancelAll();
        frames.forEach((f) => URL.revokeObjectURL(f.url));
        frames = []; hashes = new Set(); st = null; size = null; seq = 0; key = null; anchor = null; merged = 0; hiddenSeen = false; hiddenAt = null;
        stalled = false; noticeCount = 0; path = null;
      },
      get running() { return running(); },
      get hiddenSeen() { return hiddenSeen; },
      get merged() { return merged; },
      get frames() { return frames; },
      get path() { return path; },
      get stalled() { return stalled; },
      get noticeCount() { return noticeCount; },
      manifest(scope, plane) {
        return {
          schema_version: 1, plane, image_scope: scope, capture_intent: "scroll", capture_profile_id: path === "interval" ? PROFILE.id + "_interval" : PROFILE.id,
          frames: frames.map((f, i) => ({ sequence: f.sequence, file_index: i, captured_at_ms: Math.max(0, Math.round(f.capturedAt)) })),
        };
      },
    };
  }

  return { PROFILE, sigDiff, isNewCandidate, settleDecision, step, confirmStep, phase, targetSize, createCollector };
});
