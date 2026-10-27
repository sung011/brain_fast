// ② 로그인 칸 확인: /learn/knee 에서 로그인이 필요할 때 화면에서 바로 로그인하고 문제를 여는지(실제 POST /login, 시험 서버·임시 DB).
// 시험 서버를 먼저 켠다: python tests/knee_screen_harness.py --port 8012   (HARNESS_READY 줄의 login_idx/login_id/login_pw 를 인자로 넘긴다)
// 실행: node tests/browser/login_check.mjs http://127.0.0.1:8012 '{"study":1,"login_idx":4,"login_id":"...","login_pw":"..."}'
import { launch, wait } from "./cdp.mjs";
import { join } from "node:path";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";

const BASE = process.argv[2] || "http://127.0.0.1:8012";
const IDS = JSON.parse(process.argv[3] || "{}");
const OUT = process.env.SCREENSHOT_DIR || join(tmpdir(), "knee_screen_shots");
mkdirSync(OUT, { recursive: true });

let failures = 0;
function check(name, cond, detail = "") {
  console.log(`${cond ? "OK " : "!! "}${name}${detail ? "  " + detail : ""}`);
  if (!cond) failures++;
}

const b = await launch({ width: 1280, height: 900, port: 9777 });
const text = (id) => b.eval(`(document.getElementById('${id}')||{}).textContent||''`);
const shown = (id) => b.eval(`(()=>{const e=document.getElementById('${id}'); return !!e && !e.hidden && e.getClientRects().length>0})()`);
const disabled = (id) => b.eval(`(document.getElementById('${id}')||{disabled:true}).disabled`);
const api = (path, method = "GET") => b.eval(`fetch(${JSON.stringify(path)},{method:${JSON.stringify(method)},credentials:'same-origin'}).then(r=>r.json())`);
const reviews = () => api("/_reviews");
const wrapRect = () => b.eval(`(()=>{const r=document.getElementById('canvasWrap').getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height}})()`);
const boxPct = () => b.eval(`(()=>{const e=document.getElementById('userBox'); if(!e||e.hidden) return null; const s=e.style; return {x:parseFloat(s.left)/100,y:parseFloat(s.top)/100,w:parseFloat(s.width)/100,h:parseFloat(s.height)/100}})()`);
async function drawRel(fx1, fy1, fx2, fy2) {
  await b.eval(`window.scrollTo(0, 0)`);   // 로그인 칸 포커스 등으로 화면이 아래로 움직였어도 영상이 보이는 위치에서 그린다
  const r = await wrapRect();
  await b.drag(r.left + r.width * fx1, r.top + r.height * fy1, r.left + r.width * fx2, r.top + r.height * fy2);
}
async function instrument() {
  await b.eval(`(()=>{ if (window.__wrapped) return; window.__wrapped=true; window.__logins=0; window.__loginBodies=[];
    const f=window.fetch; window.fetch=function(u,i){ if(String(u).endsWith('/login')){ window.__logins++; } return f.apply(this,arguments); }; })()`);
}
async function typeInto(id, value) {
  await b.eval(`(()=>{const e=document.getElementById('${id}'); e.focus(); e.value=${JSON.stringify(value)}; e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
}
const submitLogin = () => b.eval(`document.getElementById('loginForm').requestSubmit()`);
async function waitLoaded() { await b.waitFor(`!document.getElementById('canvasWrap').hidden`); }
async function openPage() {
  await b.goto(`${BASE}/learn/knee?study_idx=${IDS.study}`);
  await wait(400);
  await instrument();
}
async function offline(on) {
  await b.send("Network.enable");
  await b.send("Network.emulateNetworkConditions", { offline: on, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
}

try {
  await b.send("Network.enable");
  await b.send("Network.setCacheDisabled", { cacheDisabled: true });
  await b.goto(`${BASE}/learn/knee?demo=1`);
  check("시연 화면에는 로그인 칸이 없다", !(await b.eval(`!!document.getElementById('loginForm')`)));
  await api("/_logout", "POST");

  // 1. 비로그인 → 로그인 칸
  await openPage();
  check("비로그인: 로그인 안내와 로그인 칸이 보인다", (await text("stageMessage")).includes("로그인이 필요합니다") && (await shown("loginForm")));
  check("로그인 칸: 비밀번호 입력은 가려진다(type=password)", (await b.eval(`document.getElementById('loginPw').type`)) === "password");
  check("로그인 칸: 입력칸에 name 속성이 없다", (await b.eval(`!document.getElementById('loginId').hasAttribute('name') && !document.getElementById('loginPw').hasAttribute('name')`)));
  await b.screenshot(join(OUT, "30_login_form.png"));

  // 2. 빈 값
  await submitLogin();
  await wait(200);
  check("빈 값: 안내만 하고 요청을 보내지 않는다", (await text("loginMessage")).includes("모두 입력") && (await b.eval(`window.__logins`)) === 0);

  // 3. 틀린 비밀번호
  await typeInto("loginId", IDS.login_id);
  await typeInto("loginPw", "wrong-password-xyz");
  await submitLogin();
  await b.waitFor(`document.getElementById('loginMessage').textContent.length>0`);
  const bodyText = await b.eval(`document.body.innerText`);
  check("틀린 비밀번호: 고정 안내 문구", (await text("loginMessage")) === "아이디 또는 비밀번호가 올바르지 않습니다.", await text("loginMessage"));
  check("틀린 비밀번호: 입력한 비밀번호·서버의 개발용 필드가 화면에 나오지 않는다", !bodyText.includes("wrong-password-xyz") && !bodyText.includes("plain_pw") && !bodyText.includes("hashed_pw"));
  check("틀린 비밀번호: 비밀번호 칸은 비워진다", (await b.eval(`document.getElementById('loginPw').value`)) === "");
  check("틀린 비밀번호: 로그인 칸은 그대로 남고 영상은 안 열린다", (await shown("loginForm")) && !(await shown("canvasWrap")));
  check("틀린 비밀번호: 요청은 1번", (await b.eval(`window.__logins`)) === 1);

  // 4. 올바른 로그인 → 문제 열림
  await typeInto("loginPw", IDS.login_pw);
  await b.eval(`(()=>{const f=document.getElementById('loginForm'); f.requestSubmit(); f.requestSubmit(); f.requestSubmit();})()`);   // 연타
  await b.waitFor(`!document.getElementById('canvasWrap').hidden`);
  check("올바른 로그인: 영상이 열린다", await shown("canvasWrap"));
  check("올바른 로그인: 연타해도 로그인 요청은 1번 더(총 2번)", (await b.eval(`window.__logins`)) === 2, String(await b.eval(`window.__logins`)));
  check("올바른 로그인: 로그인 칸이 사라지고 비워진다", !(await shown("loginForm")) && (await b.eval(`document.getElementById('loginPw').value === '' && document.getElementById('loginId').value === ''`)));
  check("올바른 로그인: 제출 전 상태", await disabled("btnSubmit"));
  const sess = await api("/_session");
  check("서버 세션은 최소 정보(회원 번호·승인 표시)뿐", JSON.stringify(sess) === JSON.stringify({ learner: { idx: IDS.login_idx, email_verified: true } }), JSON.stringify(sess));
  await b.screenshot(join(OUT, "31_login_then_task.png"));

  // 5. 로그인 뒤 채점 → 로그인한 회원 번호로 저장
  await drawRel(0.3, 0.3, 0.7, 0.7);
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`['기준 영역과 일치','일부 일치','차이 있음'].includes(document.getElementById('resultVerdict').textContent)`);
  const rows = await reviews();
  check("채점 결과가 로그인한 회원 번호로 저장된다", rows.length >= 1 && rows.at(-1).user === IDS.login_idx, JSON.stringify(rows.at(-1)));

  // 6. 저장소·주소에 비밀번호가 남지 않는다
  const leaks = await b.eval(`JSON.stringify({href: location.href, ls: Object.keys(localStorage).length, ss: Object.keys(sessionStorage).length, cookie: document.cookie})`);
  check("주소·localStorage·sessionStorage·쿠키(JS 접근)에 비밀번호가 없다", !leaks.includes(IDS.login_pw) && JSON.parse(leaks).ls === 0 && JSON.parse(leaks).ss === 0, leaks.slice(0, 120));

  // 7. 작업 중 세션이 끊김 → 같은 페이지에서 로그인(새로고침·다시 열기 없이) → 박스 초안 보존 [Codex 20차 SCREEN-020-03]
  await b.eval(`document.getElementById('btnRetry').click()`);
  await drawRel(0.35, 0.35, 0.65, 0.65);
  const draft = await boxPct();
  await b.eval(`window.__sameDocument = Math.random()`);
  const marker = await b.eval(`window.__sameDocument`);
  await api("/_logout", "POST");
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
  check("세션이 끊긴 뒤 제출: 로그인 안내(정답/오답 아님), 박스 보존", (await text("resultMessage")).includes("로그인이 필요합니다") && JSON.stringify(await boxPct()) === JSON.stringify(draft));
  check("제출 401: **같은 페이지에** 로그인 칸이 보인다(새로고침 불필요)", await shown("loginForm"));
  check("제출 401: 영상과 박스는 그대로 보인다", (await shown("canvasWrap")) && (await shown("userBox")));
  await b.screenshot(join(OUT, "32_login_same_page_after_401.png"));
  const rowsBefore7 = (await reviews()).length;
  await typeInto("loginId", IDS.login_id);
  await typeInto("loginPw", IDS.login_pw);
  await submitLogin();
  await b.waitFor(`document.getElementById('loginForm').hidden && !document.getElementById('canvasWrap').hidden && !document.getElementById('userBox').hidden`);
  check("같은 페이지 로그인 성공: 페이지는 그대로(새로 열지 않음)", (await b.eval(`window.__sameDocument`)) === marker);
  const restored = await boxPct();
  check("같은 영상이면 박스 초안이 그대로 돌아온다", restored && ["x", "y", "w", "h"].every((k) => Math.abs(restored[k] - draft[k]) < 0.002), JSON.stringify(restored));
  check("변경 안내 없음·제출 가능", !(await shown("changeNotice")) && !(await disabled("btnSubmit")));
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`['기준 영역과 일치','일부 일치','차이 있음'].includes(document.getElementById('resultVerdict').textContent)`);
  check("로그인 뒤 제출: 채점되고 1건만 저장", (await reviews()).length === rowsBefore7 + 1);

  // 7b. 세션이 끊긴 사이 대표 영상이 바뀜 → 같은 페이지 로그인 뒤에도 이전 박스는 자동 이관되지 않는다
  await b.eval(`document.getElementById('btnRetry').click()`);
  await drawRel(0.35, 0.35, 0.65, 0.65);
  const draft7b = await boxPct();
  await api("/_logout", "POST");
  await api("/_swap_reference?rep=13&reference_id=8&version=v2", "POST");
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
  check("7b 제출 401(대표가 바뀐 상태): 로그인 칸 보임·박스 보존", (await shown("loginForm")) && JSON.stringify(await boxPct()) === JSON.stringify(draft7b));
  await typeInto("loginId", IDS.login_id);
  await typeInto("loginPw", IDS.login_pw);
  await submitLogin();
  await b.waitFor(`document.getElementById('sliceInfo').textContent.includes('14번째')`);
  await wait(200);
  check("7b 대표가 바뀌면 이전 박스를 자동으로 올리지 않는다", (await boxPct()) === null);
  check("7b 변경 안내가 보이고 선택 전에는 제출할 수 없다", (await shown("changeNotice")) && (await disabled("btnSubmit")));
  const rowsBefore7b = (await reviews()).length;
  await b.eval(`document.getElementById('btnUseOldBox').click()`);
  await wait(200);
  check("7b '이전 박스 사용(직접 확인)'을 누르면 제출 가능", !(await disabled("btnSubmit")));
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`['기준 영역과 일치','일부 일치','차이 있음'].includes(document.getElementById('resultVerdict').textContent)`);
  check("7b 직접 확인 뒤 제출: 새 영상(14번째) 기준으로 1건 저장", (await reviews()).length === rowsBefore7b + 1 && (await reviews()).at(-1).image === "/_knee_assets/study/013.png");
  await api("/_swap_reference?rep=12&reference_id=7&version=v1", "POST");
  await openPage();
  await waitLoaded();

  // 8. 로그인 요청이 통신 실패 → 안내 후 복구
  await api("/_logout", "POST");
  await openPage();
  await b.waitFor(`!document.getElementById('loginForm').hidden`);
  await typeInto("loginId", IDS.login_id);
  await typeInto("loginPw", IDS.login_pw);
  await offline(true);
  await submitLogin();
  await b.waitFor(`document.getElementById('loginMessage').textContent.length>0`);
  check("로그인 통신 실패: 안내 문구, 비밀번호는 비워진다", (await text("loginMessage")).includes("통신하지 못했습니다") && (await b.eval(`document.getElementById('loginPw').value`)) === "");
  await offline(false);
  await typeInto("loginPw", IDS.login_pw);
  await submitLogin();
  await b.waitFor(`!document.getElementById('canvasWrap').hidden`);
  check("복구 후 다시 로그인하면 열린다", await shown("canvasWrap"));

  // 9. 열기 중 로그인 칸은 보이지 않는다(다른 오류에서는 나오지 않음)
  await api("/_fault?provider=1", "POST");
  await openPage();
  await wait(600);
  check("로그인 오류가 아닌 열기 실패(503)에서는 로그인 칸이 아니라 '다시 시도'만 보인다", !(await shown("loginForm")) && (await shown("btnStageRetry")));
  await api("/_fault?provider=0", "POST");

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
