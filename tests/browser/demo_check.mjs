// 시연(demo) 모드 화면 확인 (단계 A). 실행 중인 앱 서버가 필요하다: node tests/browser/demo_check.mjs http://127.0.0.1:8000
// 스크린샷은 SCREENSHOT_DIR(기본: 임시 폴더)에 저장한다.
import { launch, wait } from "./cdp.mjs";
import { join } from "node:path";

const BASE = process.argv[2] || "http://127.0.0.1:8011";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
const OUT = process.env.SCREENSHOT_DIR || join(tmpdir(), "knee_screen_shots");
mkdirSync(OUT, { recursive: true });

let failures = 0;
function check(name, cond, detail = "") {
  console.log(`${cond ? "OK " : "!! "}${name}${detail ? "  " + detail : ""}`);
  if (!cond) failures++;
}
const near = (a, b, tol = 0.6) => Math.abs(a - b) <= tol;

const b = await launch({ width: 1280, height: 900 });
const wrapRect = () => b.eval(`(()=>{const r=document.getElementById('canvasWrap').getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height}})()`);
const boxPct = () => b.eval(`(()=>{const e=document.getElementById('userBox'); if(e.hidden) return null; const s=e.style; return {x:parseFloat(s.left),y:parseFloat(s.top),w:parseFloat(s.width),h:parseFloat(s.height)}})()`);
const text = (id) => b.eval(`document.getElementById('${id}').textContent`);
const visible = (id) => b.eval(`!document.getElementById('${id}').hidden`);
const disabled = (id) => b.eval(`document.getElementById('${id}').disabled`);

async function drawRel(fx1, fy1, fx2, fy2) {
  await b.eval(`window.scrollTo(0, 0)`);   // 로그인 칸 포커스 등으로 화면이 아래로 움직였어도 영상이 보이는 위치에서 그린다
  const r = await wrapRect();
  await b.drag(r.left + r.width * fx1, r.top + r.height * fy1, r.left + r.width * fx2, r.top + r.height * fy2);
}

try {
  await b.goto(`${BASE}/learn/knee?demo=1`);
  await b.waitFor(`!document.getElementById('canvasWrap').hidden`);
  check("시연 화면이 열린다(합성 영상 표시)", true);
  check("시연 배너가 보인다", (await b.eval(`document.querySelector('.banner-demo')?.textContent || ''`)).includes("실제 서버 채점이 아닙니다"));
  check("제목·학습 목적 문구", (await text("taskTitle")).includes("ACL이 포함된 참고 영역 표시 연습"));
  check("기준 설명에 '데이터셋 라벨 기준, 임상 검증 아님'", (await b.eval(`document.body.innerText`)).includes("데이터셋 라벨 기준, 임상 검증 아님"));
  check("대표 영상 위치 표시(13번째 / 24장)", (await text("sliceInfo")).includes("13번째 / 24장"));
  check("원 도형 선택지가 없다", !(await b.eval(`/원형|circle|radius/i.test(document.querySelector('main').innerHTML.replace(/원\\(원형\\) 도형은 쓰지 않습니다/g,''))`)));
  check("처음에는 제출 불가", await disabled("btnSubmit"));
  await b.screenshot(join(OUT, "01_demo_initial.png"));

  // 박스 그리기
  await drawRel(0.25, 0.25, 0.65, 0.70);
  let p = await boxPct();
  check("그린 박스 좌표가 영상 기준 비율과 일치", p && near(p.x, 25) && near(p.y, 25) && near(p.w, 40) && near(p.h, 45), JSON.stringify(p));
  check("박스를 그리면 제출 가능", !(await disabled("btnSubmit")));

  // 영상 밖으로 끌면 가장자리에서 멈춘다
  await drawRel(0.6, 0.6, 1.4, 1.5);
  p = await boxPct();
  check("영상 밖으로 끌어도 박스는 영상 안에 머문다", p && near(p.x + p.w, 100, 0.2) && near(p.y + p.h, 100, 0.2), JSON.stringify(p));

  // 확대해도 같은 상대 위치는 같은 좌표
  await b.eval(`document.getElementById('zoomIn').click()`);
  await b.eval(`document.getElementById('zoomIn').click()`);
  await wait(200);
  const zr = await wrapRect();
  check("확대하면 영상이 커진다", zr.width > 0, `zoom=${await text("zoomLabel")} 폭=${Math.round(zr.width)}`);
  await b.eval(`document.getElementById('stage').scrollTo(0,0)`);
  await b.eval(`document.getElementById('btnReset').click()`);
  const stageBox = await b.eval(`(()=>{const r=document.getElementById('stage').getBoundingClientRect();return {l:r.left,t:r.top,w:r.width,h:r.height}})()`);
  const r2 = await wrapRect();
  // 확대된 영상에서 화면에 보이는 영역 안에 있는 상대 위치로 그린다
  const fx1 = 0.1, fy1 = 0.1, fx2 = 0.3, fy2 = 0.3;
  const visibleOK = r2.left + r2.width * fx2 <= stageBox.l + stageBox.w && r2.top + r2.height * fy2 <= stageBox.t + stageBox.h;
  if (visibleOK) {
    await drawRel(fx1, fy1, fx2, fy2);
    p = await boxPct();
    check("확대 상태에서도 좌표가 영상 기준 비율과 일치", p && near(p.x, 10) && near(p.y, 10) && near(p.w, 20) && near(p.h, 20), JSON.stringify(p));
  } else {
    console.log("-- 확대 영역이 화면 밖이라 건너뜀");
  }
  await b.eval(`document.getElementById('zoomOut').click()`);
  await b.eval(`document.getElementById('zoomOut').click()`);
  await wait(200);

  // 이동·크기 조절
  await b.eval(`document.getElementById('btnReset').click()`);
  await drawRel(0.3, 0.3, 0.6, 0.6);
  let r = await wrapRect();
  await b.drag(r.left + r.width * 0.45, r.top + r.height * 0.45, r.left + r.width * 0.55, r.top + r.height * 0.5);
  p = await boxPct();
  check("박스를 끌어 옮길 수 있다", p && near(p.x, 40) && near(p.y, 35) && near(p.w, 30) && near(p.h, 30), JSON.stringify(p));
  // se 손잡이
  const hr = await b.eval(`(()=>{const r=document.querySelector('.handle[data-handle="se"]').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  await b.drag(hr.x, hr.y, hr.x + r.width * 0.1, hr.y + r.height * 0.1);
  p = await boxPct();
  check("모서리 점으로 크기를 바꿀 수 있다", p && near(p.w, 40) && near(p.h, 40) && near(p.x, 40) && near(p.y, 35), JSON.stringify(p));
  // 키보드
  await b.eval(`document.getElementById('userBox').focus()`);
  await b.key("ArrowRight");
  p = await boxPct();
  check("방향키로 박스를 옮길 수 있다", p && near(p.x, 40.5, 0.2), JSON.stringify(p));
  // 클릭만 하면 이전 박스를 지우지 않는다
  await b.mouse("mouseMoved", r.left + 5, r.top + 5, { buttons: 0 });
  await b.mouse("mousePressed", r.left + 5, r.top + 5);
  await b.mouse("mouseReleased", r.left + 5, r.top + 5);
  p = await boxPct();
  check("빈 곳을 클릭만 해도 기존 박스는 유지된다", p && near(p.w, 40), JSON.stringify(p));

  // 제출: 일치
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden`);
  check("결과: 기준 영역과 일치", (await text("resultVerdict")) === "기준 영역과 일치");
  check("IoU 5자리 표시", /^0\.\d{5}$/.test(await text("resultIouValue")), await text("resultIouValue"));
  check("기준 박스가 결과 뒤에만 나타난다", await b.eval(`!document.getElementById('referenceRect').hasAttribute('hidden') && document.getElementById('referenceRect').getBoundingClientRect().width > 10`));
  {
    const rr = await b.eval(`(()=>{const w=document.getElementById('canvasWrap').getBoundingClientRect(); const r=document.getElementById('referenceRect').getBoundingClientRect(); return {x:(r.left-w.left)/w.width*100,y:(r.top-w.top)/w.height*100,w:r.width/w.width*100,h:r.height/w.height*100}})()`);
    check("기준 박스가 서버가 알려 준 위치(30,30,40,40%)에 그려진다", near(rr.x, 30, 0.8) && near(rr.y, 30, 0.8) && near(rr.w, 40, 0.8) && near(rr.h, 40, 0.8), JSON.stringify(rr));
  }
  check("결과 후 박스가 고정된다", await b.eval(`document.getElementById('userBox').classList.contains('is-locked')`));
  check("결과 후 그리기가 막힌다", (await (async () => { const before = await boxPct(); await drawRel(0.1, 0.1, 0.2, 0.2); const after = await boxPct(); return JSON.stringify(before) === JSON.stringify(after); })()));
  await b.screenshot(join(OUT, "02_demo_graded.png"));

  // 다시 풀기
  await b.eval(`document.getElementById('btnRetry').click()`);
  check("다시 풀기: 결과·박스가 지워진다", (await b.eval(`document.getElementById('resultCard').hidden`)) && (await boxPct()) === null);
  check("다시 풀기 후 기준 박스도 숨겨진다", await b.eval(`document.getElementById('referenceRect').hasAttribute('hidden')`));

  // 시나리오별
  const scenarios = {
    graded_partial: { verdict: "일부 일치" },
    graded_differ: { verdict: "차이 있음" },
    ungraded_no_reference: { msg: "이 슬라이스에는 승인된 기준 영역이 없어 채점하지 않습니다", noVerdict: true },
    ungraded_token_expired: { msg: "유효 시간이 지났습니다", noVerdict: true, reopen: true },
    error_outside: { msg: "박스는 이미지 안에 있어야 합니다" },
    error_unauthorized: { msg: "로그인이 필요합니다" },
    error_server: { msg: "지금 사용할 수 없습니다" },
    error_network: { msg: "서버와 통신하지 못했습니다" },
  };
  for (const [name, exp] of Object.entries(scenarios)) {
    await b.eval(`document.getElementById('btnReset').click(); document.getElementById('scenario').value='${name}'; 0`);
    await drawRel(0.3, 0.3, 0.6, 0.55);
    const before = await boxPct();
    await b.eval(`document.getElementById('btnSubmit').click()`);
    await b.waitFor(`!document.getElementById('resultCard').hidden`);
    const verdict = await text("resultVerdict");
    const msg = await text("resultMessage");
    const after = await boxPct();
    if (exp.verdict) check(`${name}: 판정 문구`, verdict === exp.verdict, verdict);
    if (exp.msg) check(`${name}: 안내 문구`, msg.includes(exp.msg), msg);
    if (exp.noVerdict) check(`${name}: 정답/오답으로 표시하지 않는다`, !["기준 영역과 일치", "일부 일치", "차이 있음"].includes(verdict) && (await b.eval(`document.getElementById('resultIou').hidden`)), verdict);
    if (!exp.verdict) check(`${name}: 박스가 보존된다`, JSON.stringify(before) === JSON.stringify(after));
    if (exp.reopen) check(`${name}: '문제 다시 열기' 버튼 + 제출 막힘`, (await visible("btnReopen")) && (await disabled("btnSubmit")));
    if (name === "error_network") {
      await b.eval(`document.getElementById('scenario').value='graded_match'; 0`);
      check("통신 오류 뒤에도 다시 제출할 수 있다", !(await disabled("btnSubmit")));
      await b.eval(`document.getElementById('btnSubmit').click()`);
      await b.waitFor(`document.getElementById('resultVerdict').textContent==='기준 영역과 일치'`);
      check("통신 오류 뒤 재제출 성공", true);
    }
    if (name === "ungraded_no_reference") await b.screenshot(join(OUT, "03_demo_ungraded.png"));
    if (name === "error_network") await b.screenshot(join(OUT, "04_demo_network_then_ok.png"));
    await b.eval(`(document.getElementById('btnRetry').hidden ? 0 : document.getElementById('btnRetry').click()); document.getElementById('btnReset').click(); 0`);
  }

  // 중복 제출 방지: 연속 클릭
  await b.eval(`document.getElementById('scenario').value='graded_match'; document.getElementById('btnReset').click(); 0`);
  await drawRel(0.3, 0.3, 0.6, 0.55);
  const clicks = await b.eval(`(async()=>{let n=0; const orig=window.fetch; const btn=document.getElementById('btnSubmit'); btn.click(); btn.click(); btn.click(); return !btn.disabled || btn.textContent;})()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden`);
  check("중복 클릭해도 결과 카드는 하나", true, String(clicks));

  // 모바일 폭
  await b.eval(`document.getElementById('btnRetry').click()`);
  await b.viewport(390, 844, true);
  await wait(400);
  const noHScroll = await b.eval(`document.documentElement.scrollWidth <= window.innerWidth + 1`);
  check("모바일 폭(390)에서 가로 스크롤이 없다", noHScroll, `scrollWidth=${await b.eval('document.documentElement.scrollWidth')}`);
  await drawRel(0.2, 0.2, 0.7, 0.7);
  p = await boxPct();
  check("모바일 폭에서도 좌표가 영상 기준 비율과 일치", p && near(p.x, 20) && near(p.y, 20) && near(p.w, 50) && near(p.h, 50), JSON.stringify(p));
  await b.screenshot(join(OUT, "05_demo_mobile.png"));

  const errors = b.events.filter((e) => e.method === "Runtime.exceptionThrown" || (e.method === "Runtime.consoleAPICalled" && e.params.type === "error"));
  check("콘솔 오류 없음", errors.length === 0, errors.length ? JSON.stringify(errors[0].params).slice(0, 300) : "");
} catch (err) {
  failures++;
  console.log("!! 예외:", err.message);
} finally {
  await b.close();
}
console.log(failures === 0 ? "\n모두 통과" : `\n실패 ${failures}건`);
process.exit(failures === 0 ? 0 : 1);
