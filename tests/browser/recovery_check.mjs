// FINDING-SCREEN-017-01/02 회귀 확인: 문제 열기 실패 뒤 재시도 경로, 박스 초안 보존, 영상이 바뀌면 이전 박스 자동 이관 금지.
// 시험 서버를 먼저 켠다: python tests/knee_screen_harness.py --port 8012
// 실행: node tests/browser/recovery_check.mjs http://127.0.0.1:8012
import { launch, wait } from "./cdp.mjs";
import { join } from "node:path";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";

const BASE = process.argv[2] || "http://127.0.0.1:8012";
const IDS = JSON.parse(process.argv[3] || '{"study":1,"user":1,"other":2}');
const OUT = process.env.SCREENSHOT_DIR || join(tmpdir(), "knee_screen_shots");
mkdirSync(OUT, { recursive: true });

let failures = 0;
function check(name, cond, detail = "") {
  console.log(`${cond ? "OK " : "!! "}${name}${detail ? "  " + detail : ""}`);
  if (!cond) failures++;
}
const near = (a, b, tol) => Math.abs(a - b) <= tol;

const b = await launch({ width: 1280, height: 900, port: 9666 });
const text = (id) => b.eval(`(document.getElementById('${id}')||{}).textContent||''`);
const exists = (id) => b.eval(`!!document.getElementById('${id}')`);
const shown = (id) => b.eval(`(()=>{const e=document.getElementById('${id}'); return !!e && !e.hidden && e.getClientRects().length>0})()`);
const disabled = (id) => b.eval(`(document.getElementById('${id}')||{disabled:true}).disabled`);
const click = (id) => b.eval(`(()=>{const e=document.getElementById('${id}'); if(!e) return false; e.click(); return true})()`);
const wrapRect = () => b.eval(`(()=>{const r=document.getElementById('canvasWrap').getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height}})()`);
const boxPct = () => b.eval(`(()=>{const e=document.getElementById('userBox'); if(!e||e.hidden) return null; const s=e.style; return {x:parseFloat(s.left)/100,y:parseFloat(s.top)/100,w:parseFloat(s.width)/100,h:parseFloat(s.height)/100}})()`);
const sameBox = (a, c) => a && c && ["x", "y", "w", "h"].every((k) => near(a[k], c[k], 0.002));
const api = (path, method = "GET") => b.eval(`fetch(${JSON.stringify(path)},{method:${JSON.stringify(method)},credentials:'same-origin'}).then(r=>r.json())`);
const reviews = () => api("/_reviews");
const slice = () => text("sliceInfo");

async function drawRel(fx1, fy1, fx2, fy2) {
  await b.eval(`window.scrollTo(0, 0)`);   // 로그인 칸 포커스 등으로 화면이 아래로 움직였어도 영상이 보이는 위치에서 그린다
  const r = await wrapRect();
  await b.drag(r.left + r.width * fx1, r.top + r.height * fy1, r.left + r.width * fx2, r.top + r.height * fy2);
}
async function instrument() {
  await b.eval(`(()=>{ if (window.__wrapped) return; window.__wrapped=true; window.__tasks=0;
    const f=window.fetch; window.fetch=function(u){ if(String(u).includes('/learning/knee/task')) window.__tasks++; return f.apply(this,arguments); }; })()`);
}
async function openPage() {
  await b.goto(`${BASE}/learn/knee?study_idx=${IDS.study}`);
  await wait(300);
  await instrument();
}
async function loginAs(idx) { await b.eval(`fetch('/_login?idx=${idx}&verified=true',{method:'POST',credentials:'same-origin'}).then(r=>r.status)`); }
async function offline(on) {
  await b.send("Network.enable");
  await b.send("Network.emulateNetworkConditions", { offline: on, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
}
async function waitLoaded() { await b.waitFor(`!document.getElementById('canvasWrap').hidden`); }
async function expireAndSubmit() {
  await api("/_advance?seconds=7300", "POST");
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
}
const VERDICTS = `['기준 영역과 일치','일부 일치','차이 있음'].includes(document.getElementById('resultVerdict').textContent)`;

async function resetWorld() {
  await offline(false);
  await api("/_fault?provider=0&image=0", "POST");
  await api("/_swap_reference?rep=12&reference_id=7&version=v1", "POST");
  await api("/_advance?reset=true", "POST");
}
async function scenario(name, fn) {
  try {
    await resetWorld();
    await loginAs(IDS.user);
    await fn();
  } catch (err) {
    failures++;
    console.log(`!! ${name} 진행 중단: ${err.message}`);
  }
}

try {
  await b.send("Network.enable");
  await b.send("Network.setCacheDisabled", { cacheDisabled: true });
  await b.goto(`${BASE}/learn/knee?demo=1`);   // 같은 Origin 에서 시험 전용 주소를 부르기 위해 먼저 연다
  await api("/_fault?provider=0&image=0", "POST");

  await scenario("S1", async () => {
    // ── S1: 만료 → 재열기 중 오프라인 → 복구 ──────────────────────────────────────
    await loginAs(IDS.user);
    await openPage();
    await waitLoaded();
    await drawRel(0.35, 0.35, 0.65, 0.65);
    const draft1 = await boxPct();
    await expireAndSubmit();
    await offline(true);
    await click("btnReopen");
    await wait(600);
    check("S1 재열기 실패: 화면에 '다시 시도' 버튼이 보인다", await shown("btnStageRetry"));
    check("S1 재열기 실패: 통신 실패 안내가 보인다", (await text("stageMessage")).includes("통신하지 못했습니다"), await text("stageMessage"));
    await offline(false);
    const before1 = (await reviews()).length;
    await click("btnStageRetry");
    await waitLoaded();
    await wait(200);
    check("S1 복구 후: 같은 영상이면 박스 초안이 그대로 돌아온다", sameBox(await boxPct(), draft1), JSON.stringify(await boxPct()));
    check("S1 복구 후: 변경 안내가 뜨지 않는다(같은 영상)", !(await shown("changeNotice")));
    check("S1 복구 후: 제출할 수 있다", !(await disabled("btnSubmit")));
    await b.eval(`document.getElementById('btnSubmit').click()`);
    await b.waitFor(VERDICTS);
    check("S1 복구 후 제출: 채점되고 1건만 저장", (await reviews()).length === before1 + 1);
    await b.screenshot(join(OUT, "20_recovery_s1.png"));
  });
  await scenario("S2", async () => {
    // ── S2: 만료 → 재열기 중 서버 오류(503) → 복구 ──────────────────────────────────
    await openPage();
    await waitLoaded();
    await drawRel(0.3, 0.3, 0.6, 0.55);
    const draft2 = await boxPct();
    await api("/_fault?provider=1", "POST");
    await expireAndSubmit();
    await click("btnReopen");
    await wait(600);
    check("S2 서버 오류: '다시 시도' 버튼이 보인다", await shown("btnStageRetry"));
    check("S2 서버 오류: 안내 문구", (await text("stageMessage")).includes("사용할 수 없습니다"), await text("stageMessage"));
    await click("btnStageRetry");
    await wait(500);
    check("S2 계속 실패해도 버튼이 남아 있다", await shown("btnStageRetry"));
    await api("/_fault?provider=0", "POST");
    await click("btnStageRetry");
    await waitLoaded();
    check("S2 복구 후: 박스 초안 보존", sameBox(await boxPct(), draft2));
    check("S2 복구 후: 제출 가능", !(await disabled("btnSubmit")));
    await click("btnReset");
  });
  await scenario("S3", async () => {
    // ── S3: 재시도 연타는 요청 1번(경쟁 방지) ──────────────────────────────────────
    await openPage();
    await waitLoaded();
    await drawRel(0.3, 0.3, 0.6, 0.55);
    await api("/_fault?provider=1", "POST");
    await expireAndSubmit();
    await click("btnReopen");
    await wait(500);
    await b.eval(`window.__tasks=0`);
    await b.eval(`(()=>{const e=document.getElementById('btnStageRetry'); if(e){e.click(); e.click(); e.click();}})()`);
    await wait(800);
    check("S3 '다시 시도'를 연타해도 문제 열기 요청은 1번", (await b.eval(`window.__tasks`)) === 1, String(await b.eval(`window.__tasks`)));
    await api("/_fault?provider=0", "POST");
    await click("btnStageRetry");
    await waitLoaded();
    await click("btnReset");
    await click("btnRetry");
  });
  await scenario("S4", async () => {
    // ── S4: 처음부터 로그인 안 됨 → 로그인 뒤 재시도 ─────────────────────────────────
    await api("/_logout", "POST");
    await openPage();
    await wait(500);
    check("S4 초기 401: 로그인 안내 + '다시 시도' 버튼", (await text("stageMessage")).includes("로그인이 필요합니다") && (await shown("btnStageRetry")));
    await loginAs(IDS.user);
    await click("btnStageRetry");
    await waitLoaded();
    check("S4 로그인 뒤 재시도: 영상이 열리고 제출 전 상태", (await slice()).includes("번째") && (await disabled("btnSubmit")));
  });
  await scenario("S5", async () => {
    // ── S5: 영상 파일 오류 → 복구 ───────────────────────────────────────────────────
    await api("/_fault?image=1", "POST");
    await openPage();
    await b.waitFor(`document.getElementById('stageMessage').textContent.includes('영상을 불러오지 못했습니다')`);
    check("S5 영상 로드 실패: '다시 시도' 버튼", await shown("btnStageRetry"));
    await api("/_fault?image=0", "POST");
    await click("btnStageRetry");
    await waitLoaded();
    check("S5 복구 후 영상이 열린다", await shown("canvasWrap"));
  });
  await scenario("S6", async () => {
    // ── S6: 같은 영상에서 기준 버전만 바뀜(presentation_changed) → 박스 보존 ────────────
    await api("/_swap_reference?rep=12&reference_id=7&version=v1", "POST");
    await openPage();
    await waitLoaded();
    await drawRel(0.35, 0.35, 0.65, 0.65);
    const draft6 = await boxPct();
    await api("/_swap_reference?rep=12&reference_id=9&version=v2", "POST");
    await b.eval(`document.getElementById('btnSubmit').click()`);
    await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
    check("S6 기준 버전 변경: 미채점 안내(정답/오답 아님)", (await text("resultMessage")).includes("바뀌어") && !(await b.eval(VERDICTS)));
    await click("btnReopen");
    await waitLoaded();
    await wait(200);
    check("S6 같은 영상: 박스 초안 보존·변경 안내 없음", sameBox(await boxPct(), draft6) && !(await shown("changeNotice")));
    check("S6 같은 영상: 제출 가능", !(await disabled("btnSubmit")));
  });
  await scenario("S7", async () => {
    // ── S7: 대표 영상 자체가 바뀜 → 이전 박스를 자동으로 옮기지 않는다 ───────────────────
    await api("/_swap_reference?rep=12&reference_id=7&version=v1", "POST");
    await openPage();
    await waitLoaded();
    await drawRel(0.35, 0.35, 0.65, 0.65);
    const draft7 = await boxPct();
    const sliceBefore = await slice();
    await api("/_swap_reference?rep=13&reference_id=8&version=v2", "POST");
    await b.eval(`document.getElementById('btnSubmit').click()`);
    await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
    check("S7 대표 변경 후 제출: 정답/오답으로 보이지 않는다", !(await b.eval(VERDICTS)));
    check("S7 대표 변경 후 제출: '문제 다시 열기' 경로가 보인다", await shown("btnReopen"));
    await click("btnReopen");
    await b.waitFor(`document.getElementById('sliceInfo').textContent.includes('14번째')`);
    await wait(200);
    check("S7 새 대표(14번째)가 열렸다", (await slice()).includes("14번째") && sliceBefore.includes("13번째"));
    check("S7 이전 박스를 새 영상에 자동으로 올리지 않는다", (await boxPct()) === null, JSON.stringify(await boxPct()));
    check("S7 변경 안내가 보이고 제출은 막혀 있다", (await shown("changeNotice")) && (await disabled("btnSubmit")));
    check("S7 안내에 선택지(이전 박스 사용 / 다시 그리기)", (await shown("btnUseOldBox")) && (await shown("btnRedraw")));
    await b.screenshot(join(OUT, "21_recovery_s7_change_notice.png"));
    // 명시적 확인: 이전 박스 사용
    const rowsBefore7 = (await reviews()).length;
    await click("btnUseOldBox");
    await wait(200);
    check("S7 '이전 박스 사용'을 눌러야 박스가 돌아오고 제출 가능", sameBox(await boxPct(), draft7) && !(await disabled("btnSubmit")) && !(await shown("changeNotice")));
    await b.eval(`document.getElementById('btnSubmit').click()`);
    await b.waitFor(VERDICTS);
    check("S7 명시 확인 뒤 제출: 새 영상(14번째) 기준으로 채점·저장", (await reviews()).length === rowsBefore7 + 1 && (await reviews()).at(-1).image === "/_knee_assets/study/013.png");
  });
  await scenario("S8", async () => {
    // ── S8: 대표 변경 → '다시 그리기' ────────────────────────────────────────────────
    await openPage();
    await waitLoaded();
    await drawRel(0.3, 0.3, 0.6, 0.55);
    await api("/_swap_reference?rep=13&reference_id=8&version=v2", "POST");
    await b.eval(`document.getElementById('btnSubmit').click()`);
    await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
    await click("btnReopen");
    await b.waitFor(`document.getElementById('sliceInfo').textContent.includes('14번째')`);
    await click("btnRedraw");
    await wait(200);
    check("S8 '다시 그리기': 안내가 닫히고 박스는 비어 있다(제출 불가)", !(await shown("changeNotice")) && (await boxPct()) === null && (await disabled("btnSubmit")));
    await drawRel(0.3, 0.3, 0.7, 0.7);
    check("S8 새로 그리면 제출 가능", !(await disabled("btnSubmit")));
  });
  await scenario("S9", async () => {
    // ── S9: 안내 문구(NOTE) ───────────────────────────────────────────────────────
    await openPage();
    await waitLoaded();
    check("S9 승인된 문제: 검수 완료 문구는 문제가 열린 상태에서만", (await text("basisReview")).includes("확인했습니다"), await text("basisReview"));
    await api("/_logout", "POST");
    await openPage();
    await wait(500);
    check("S9 문제가 열리지 않은 상태에서는 검수 완료를 단정하지 않는다", !(await text("basisReview")).includes("확인했습니다") && (await text("basisReview")).length > 0, await text("basisReview"));
    await b.goto(`${BASE}/learn/knee?demo=1`);
    await wait(500);
    check("S9 시연: 합성 영상이라 검수된 기준이 아님을 알린다", (await text("basisReview")).includes("합성") && !(await text("basisReview")).includes("확인했습니다"), await text("basisReview"));
    await loginAs(IDS.user);
    await openPage();
    await waitLoaded();
    await drawRel(0.3, 0.3, 0.6, 0.55);
    await offline(true);
    await b.eval(`document.getElementById('btnSubmit').click()`);
    await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
    const lost = await text("resultMessage");
    check("S9 응답 유실 안내: 이미 처리됐을 수 있고 다시 제출하면 기록이 한 번 더 남을 수 있다", lost.includes("이미") && lost.includes("한 번 더"), lost);
    check("S9 응답 유실 안내: 저장되지 않았다고 단정하지 않는다", !lost.includes("저장되지 않았") && !lost.includes("저장하지 못했"));
    await offline(false);
  });

  const errors = b.events.filter((e) => e.method === "Runtime.exceptionThrown");
  check("스크립트 예외 없음", errors.length === 0, errors.length ? JSON.stringify(errors[0].params).slice(0, 300) : "");
} catch (err) {
  failures++;
  console.log("!! 예외:", err.message);
} finally {
  await b.close();
}
console.log(failures === 0 ? "\n모두 통과" : `\n실패 ${failures}건`);
process.exit(failures === 0 ? 0 : 1);
