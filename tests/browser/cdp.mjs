// 최소 Chrome DevTools Protocol 도우미: 헤드리스 Edge 를 띄워 실제 마우스·키보드 입력으로 화면 동작을 시험한다.
// Node 22+ 의 내장 WebSocket 을 쓰며 추가 패키지가 필요 없다. 시험 전용이며 운영 코드가 아니다.
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const EDGE = process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";   // 다른 위치면 EDGE_PATH 로 지정
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launch({ width = 1280, height = 900, port = 9333 } = {}) {
  const profile = mkdtempSync(join(tmpdir(), "edge-cdp-"));
  const proc = spawn(EDGE, [
    "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run",
    "--disable-gpu", "--hide-scrollbars", `--window-size=${width},${height}`, "about:blank",
  ], { stdio: "ignore" });
  let targets = null;
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json`);
      targets = (await res.json()).filter((t) => t.type === "page");
      if (targets.length) break;
    } catch (e) { /* 아직 안 떴다 */ }
    await sleep(250);
  }
  if (!targets || !targets.length) { proc.kill(); throw new Error("Edge 를 시작하지 못했습니다."); }
  const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && pending.has(data.id)) {
      const { resolve, reject } = pending.get(data.id);
      pending.delete(data.id);
      data.error ? reject(new Error(JSON.stringify(data.error))) : resolve(data.result);
    } else if (data.method) {
      events.push(data);
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const myId = ++id;
    pending.set(myId, { resolve, reject });
    ws.send(JSON.stringify({ id: myId, method, params }));
  });
  await send("Page.enable");
  await send("Runtime.enable");

  const api = {
    send, events,
    async goto(url) {
      events.length = 0;
      await send("Page.navigate", { url });
      for (let i = 0; i < 80; i++) {
        if (events.some((e) => e.method === "Page.loadEventFired")) break;
        await sleep(100);
      }
    },
    async eval(expression) {
      const res = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (res.exceptionDetails) throw new Error("eval 오류: " + JSON.stringify(res.exceptionDetails.exception?.description || res.exceptionDetails.text));
      return res.result.value;
    },
    async waitFor(expression, timeout = 8000) {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        try { if (await api.eval(expression)) return true; } catch (e) { /* 재시도 */ }
        await sleep(100);
      }
      throw new Error("시간 초과: " + expression);
    },
    async mouse(type, x, y, extra = {}) {
      await send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1, ...extra });
    },
    async drag(x1, y1, x2, y2, steps = 6) {
      await api.mouse("mouseMoved", x1, y1, { buttons: 0 });
      await api.mouse("mousePressed", x1, y1);
      for (let i = 1; i <= steps; i++) await api.mouse("mouseMoved", x1 + ((x2 - x1) * i) / steps, y1 + ((y2 - y1) * i) / steps);
      await api.mouse("mouseReleased", x2, y2);
    },
    async key(key, modifiers = 0) {
      const code = key;
      await send("Input.dispatchKeyEvent", { type: "keyDown", key, code, modifiers, windowsVirtualKeyCode: { ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40 }[key] });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key, code, modifiers, windowsVirtualKeyCode: { ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40 }[key] });
    },
    async viewport(width, height, mobile = false) {
      await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
    },
    async screenshot(path) {
      const { data } = await send("Page.captureScreenshot", { format: "png" });
      writeFileSync(path, Buffer.from(data, "base64"));
    },
    async close() { try { ws.close(); } catch (e) { /* 무시 */ } proc.kill(); await sleep(300); },
  };
  return api;
}

export const wait = sleep;
