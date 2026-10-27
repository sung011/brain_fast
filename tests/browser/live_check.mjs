// 실제 서버 응답(시험 환경: 임시 DB + 시험 전용 로그인)과 연결한 화면 확인 (단계 B·C).
// 먼저 시험 서버를 켠다: python tests/knee_screen_harness.py --port 8012   (HARNESS_READY 줄의 번호를 세 번째 인자로 넘길 수 있다)
// 실행: node tests/browser/live_check.mjs http://127.0.0.1:8012
import { launch, wait } from "./cdp.mjs";
import { join } from "node:path";
import { mkdirSync } from "node:fs";

const BASE = process.argv[2] || "http://127.0.0.1:8012";
import { tmpdir } from "node:os";
const OUT = process.env.SCREENSHOT_DIR || join(tmpdir(), "knee_screen_shots");
mkdirSync(OUT, { recursive: true });
const IDS = JSON.parse(process.argv[3] || '{"study":1,"unapproved":2,"brain":3,"user":1,"other":2}');

let failures = 0;
function check(name, cond, detail = "") {
  console.log(`${cond ? "OK " : "!! "}${name}${detail ? "  " + detail : ""}`);
  if (!cond) failures++;
}
const near = (a, b, tol) => Math.abs(a - b) <= tol;

const b = await launch({ width: 1280, height: 900, port: 9444 });
const text = (id) => b.eval(`document.getElementById('${id}').textContent`);
const hiddenEl = (id) => b.eval(`document.getElementById('${id}').hidden`);
const disabled = (id) => b.eval(`document.getElementById('${id}').disabled`);
const wrapRect = () => b.eval(`(()=>{const r=document.getElementById('canvasWrap').getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height}})()`);
const boxPct = () => b.eval(`(()=>{const e=document.getElementById('userBox'); if(e.hidden) return null; const s=e.style; return {x:parseFloat(s.left)/100,y:parseFloat(s.top)/100,w:parseFloat(s.width)/100,h:parseFloat(s.height)/100}})()`);
const api = (path, method = "GET") => b.eval(`fetch(${JSON.stringify(path)},{method:${JSON.stringify(method)},credentials:'same-origin'}).then(r=>r.json())`);
const reviews = () => api("/_reviews");

async function drawRel(fx1, fy1, fx2, fy2) {
  await b.eval(`window.scrollTo(0, 0)`);   // 로그인 칸 포커스 등으로 화면이 아래로 움직였어도 영상이 보이는 위치에서 그린다
  const r = await wrapRect();
  await b.drag(r.left + r.width * fx1, r.top + r.height * fy1, r.left + r.width * fx2, r.top + r.height * fy2);
}
async function instrument() {
  // 제출 요청 수와 마지막 응답을 기록한다(서버로 가는 값 확인용). 화면 코드는 바꾸지 않는다.
  await b.eval(`(()=>{ if (window.__wrapped) return; window.__wrapped=true; window.__posts=0; window.__bodies=[]; window.__last=null;
    const f=window.fetch; window.fetch=function(u,i){ const isGrade=String(u).includes('roi-grade');
      if(isGrade){ window.__posts++; try{ const fd=i&&i.body; if(fd&&fd.entries){ const o={}; for(const [k,v] of fd.entries()) o[k]=v; window.__bodies.push(o);} }catch(e){} }
      return f.apply(this,arguments).then(res=>{ if(isGrade){ res.clone().json().then(j=>{window.__last={status:res.status,body:j}}).catch(()=>{}); } return res; }); }; })()`);
}
const lastResponse = async () => { await wait(150); return b.eval(`window.__last`); };

async function openPage(study) {
  await b.goto(`${BASE}/learn/knee?study_idx=${study}`);
  await wait(300);
}
async function loginAs(idx, verified = true) {
  await b.eval(`fetch('/_login?idx=${idx}&verified=${verified}',{method:'POST',credentials:'same-origin'}).then(r=>r.status)`);
}

try {
  // 1. 비로그인
  await b.goto(`${BASE}/learn/knee?study_idx=${IDS.study}`);
  await b.eval(`fetch('/_logout',{method:'POST',credentials:'same-origin'}).then(()=>0)`);
  await openPage(IDS.study);
  check("비로그인: 영상 대신 로그인 안내", (await text("stageMessage")).includes("로그인이 필요합니다") && (await hiddenEl("canvasWrap")));
  check("비로그인: 제출 불가", await disabled("btnSubmit"));
  await b.screenshot(join(OUT, "10_live_not_logged_in.png"));

  // 2. 로그인 + 정상 흐름
  await loginAs(IDS.user);
  await openPage(IDS.study);
  await b.waitFor(`!document.getElementById('canvasWrap').hidden`);
  check("실제 영상(대표 장)이 열린다", true);
  check("대표 영상 위치 표시 13번째 / 24장", (await text("sliceInfo")).includes("13번째 / 24장"), await text("sliceInfo"));
  const r0 = await wrapRect();
  check("영상 비율이 서버 크기(320×384)와 같다(여백 없음)", near(r0.width / r0.height, 320 / 384, 0.01), `${r0.width.toFixed(1)}x${r0.height.toFixed(1)}`);
  check("영상 파일 크기 확인", await b.eval(`document.getElementById('studyImage').naturalWidth===320 && document.getElementById('studyImage').naturalHeight===384`));
  await instrument();
  await drawRel(0.3, 0.3, 0.7, 0.7);
  const drawn1 = await boxPct();
  // 더블 클릭: 요청은 한 번만
  await b.eval(`(()=>{const b=document.getElementById('btnSubmit'); b.click(); b.click(); b.click();})()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultVerdict').textContent!==''`);
  await wait(200);
  check("연속 클릭해도 제출 요청은 1번", (await b.eval(`window.__posts`)) === 1, String(await b.eval(`window.__posts`)));
  const res1 = await lastResponse();
  check("서버가 채점했다(graded=true, 200)", res1.status === 200 && res1.body.graded === true, JSON.stringify({ r: res1.body.result, iou: res1.body.iou }));
  const sent = (await b.eval(`window.__bodies`))[0];
  check("보낸 값: 문제·도형·대표 번호·토큰", sent.study_idx === String(IDS.study) && sent.roi_type === "box" && sent.slice_index === "12" && sent.problem_token.startsWith("kt1."), JSON.stringify({ ...sent, problem_token: "…" }));
  check("보낸 값에 사용자 번호·경로·해시가 없다", !("user_idx" in sent) && !("image_path" in sent) && !("rn_image" in sent));
  const srv = res1.body.user_roi_bbox;
  check("그린 박스 = 서버가 채점한 위치", near(srv.x, drawn1.x, 1 / 512 + 1e-6) && near(srv.y, drawn1.y, 1 / 512 + 1e-6) && near(srv.width, drawn1.w, 1 / 512 + 1e-6) && near(srv.height, drawn1.h, 1 / 512 + 1e-6), `화면 ${JSON.stringify(drawn1)} 서버 ${JSON.stringify(srv)}`);
  const verdictText = await text("resultVerdict");
  const expectLabel = { "정답": "기준 영역과 일치", "부분정답": "일부 일치", "오답": "차이 있음" }[res1.body.result];
  check("화면 판정 문구 = 서버 판정", verdictText === expectLabel, `${verdictText} / 서버 ${res1.body.result}`);
  check("화면 IoU = 서버 IoU(5자리)", (await text("resultIouValue")) === res1.body.iou.toFixed(5), await text("resultIouValue"));
  const refDom = await b.eval(`(()=>{const w=document.getElementById('canvasWrap').getBoundingClientRect(); const r=document.getElementById('referenceRect').getBoundingClientRect(); return {x:(r.left-w.left)/w.width,y:(r.top-w.top)/w.height,w:r.width/w.width,h:r.height/w.height}})()`);
  check("기준 박스는 서버 응답 위치에 표시", near(refDom.x, 0.3, 0.005) && near(refDom.y, 0.3, 0.005) && near(refDom.w, 0.4, 0.005) && near(refDom.h, 0.4, 0.005), JSON.stringify(refDom));
  let rows = await reviews();
  check("임시 DB 에 1건 저장(대표 장 단일 경로)", rows.length === 1 && rows[0].user === IDS.user && rows[0].study === IDS.study && rows[0].image === "/_knee_assets/study/012.png", JSON.stringify(rows));
  check("저장된 번호 = 응답 review_idx", rows.length === 1 && rows[0].idx === res1.body.review_idx);
  await b.screenshot(join(OUT, "11_live_graded.png"));

  // 3. 다시 풀기 → 확대 상태에서 다른 박스(일부 겹침)
  await b.eval(`document.getElementById('btnRetry').click()`);
  await b.eval(`document.getElementById('zoomIn').click(); document.getElementById('zoomIn').click()`);
  await wait(200);
  await b.eval(`document.getElementById('stage').scrollTo(0,0)`);
  await drawRel(0.5, 0.2, 0.7, 0.45);
  const drawn2 = await boxPct();
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`document.getElementById('resultVerdict').textContent!=='' && !document.getElementById('resultCard').hidden`);
  const res2 = await lastResponse();
  check("확대 상태에서도 그린 박스 = 서버가 채점한 위치", res2.body.graded && near(res2.body.user_roi_bbox.x, drawn2.x, 0.003) && near(res2.body.user_roi_bbox.y, drawn2.y, 0.003) && near(res2.body.user_roi_bbox.width, drawn2.w, 0.003) && near(res2.body.user_roi_bbox.height, drawn2.h, 0.003), `화면 ${JSON.stringify(drawn2)} 서버 ${JSON.stringify(res2.body.user_roi_bbox)}`);
  check("두 번째 제출도 저장(총 2건)", (await reviews()).length === 2);
  await b.eval(`document.getElementById('btnRetry').click(); document.getElementById('zoomOut').click(); document.getElementById('zoomOut').click()`);
  await wait(200);

  // 4. 작은 창(여백/비율 변화) 에서도 좌표가 같다
  await b.viewport(700, 520);
  await wait(400);
  await drawRel(0.1, 0.6, 0.5, 0.95);
  const drawn3 = await boxPct();
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`document.getElementById('resultVerdict').textContent!=='' && !document.getElementById('resultCard').hidden`);
  const res3 = await lastResponse();
  check("작은 창에서도 그린 박스 = 서버가 채점한 위치", res3.body.graded && near(res3.body.user_roi_bbox.x, drawn3.x, 0.003) && near(res3.body.user_roi_bbox.y, drawn3.y, 0.003) && near(res3.body.user_roi_bbox.width, drawn3.w, 0.003) && near(res3.body.user_roi_bbox.height, drawn3.h, 0.003));
  await b.eval(`document.getElementById('btnRetry').click()`);
  await b.viewport(1280, 900);
  await wait(300);

  // 5. 도중 로그아웃 → 401 안내, 박스 보존, 재로그인 후 같은 박스로 제출
  await drawRel(0.3, 0.3, 0.7, 0.7);
  const keep = await boxPct();
  const countBefore = (await reviews()).length;
  await api("/_logout", "POST");
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
  check("로그아웃 후 제출: 로그인 안내(정답/오답 아님)", (await text("resultMessage")).includes("로그인이 필요합니다") && !["기준 영역과 일치", "일부 일치", "차이 있음"].includes(await text("resultVerdict")));
  check("로그아웃 후 제출: 박스 보존·재제출 가능", JSON.stringify(await boxPct()) === JSON.stringify(keep) && !(await disabled("btnSubmit")));
  check("로그아웃 후 제출: 저장 없음", (await reviews()).length === countBefore);
  await b.screenshot(join(OUT, "12_live_401_keep_box.png"));
  await loginAs(IDS.user);
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`document.getElementById('resultVerdict').textContent==='기준 영역과 일치' || document.getElementById('resultVerdict').textContent==='일부 일치' || document.getElementById('resultVerdict').textContent==='차이 있음'`);
  check("재로그인 후 같은 박스로 제출 성공", (await reviews()).length === countBefore + 1);
  await b.eval(`document.getElementById('btnRetry').click()`);

  // 6. 통신 오프라인 → 안내, 박스 보존 → 온라인 복구 후 재제출
  await drawRel(0.35, 0.35, 0.65, 0.65);
  const keep2 = await boxPct();
  const count2 = (await reviews()).length;
  await b.send("Network.enable");
  await b.send("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
  check("오프라인 제출: 통신 실패 안내", (await text("resultMessage")).includes("통신하지 못했습니다"));
  check("오프라인 제출: 박스 보존·재제출 가능", JSON.stringify(await boxPct()) === JSON.stringify(keep2) && !(await disabled("btnSubmit")));
  await b.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`['기준 영역과 일치','일부 일치','차이 있음'].includes(document.getElementById('resultVerdict').textContent)`);
  check("복구 후 재제출 성공(저장 1건 추가, 중복 저장 없음)", (await reviews()).length === count2 + 1);
  await b.eval(`document.getElementById('btnRetry').click()`);

  // 7. 토큰 만료 → 미채점 안내 → 문제 다시 열기 → 제출 성공
  await api("/_advance?seconds=7300", "POST");
  await drawRel(0.3, 0.3, 0.7, 0.7);
  const count3 = (await reviews()).length;
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`!document.getElementById('resultCard').hidden && document.getElementById('resultMessage').textContent!==''`);
  check("만료 토큰: 미채점 안내(정답/오답 아님)", (await text("resultMessage")).includes("유효 시간이 지났습니다") && (await text("resultVerdict")) === "채점하지 않았습니다" && (await hiddenEl("resultIou")));
  check("만료 토큰: 저장 없음 + 문제 다시 열기 + 제출 막힘", (await reviews()).length === count3 && !(await hiddenEl("btnReopen")) === true && (await disabled("btnSubmit")));
  await b.screenshot(join(OUT, "13_live_token_expired.png"));
  const keep4 = await boxPct();
  await b.eval(`document.getElementById('btnReopen').click()`);
  await b.waitFor(`!document.getElementById('canvasWrap').hidden && !document.getElementById('userBox').hidden`);
  check("다시 열기: 박스 보존", JSON.stringify(await boxPct()) === JSON.stringify(keep4));
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`['기준 영역과 일치','일부 일치','차이 있음'].includes(document.getElementById('resultVerdict').textContent)`);
  check("다시 연 문제에서 채점·저장", (await reviews()).length === count3 + 1);
  await b.eval(`document.getElementById('btnRetry').click()`);

  // 8. 다른 학습자의 문제 열기/제출은 본인 번호로만 저장
  await loginAs(IDS.other);
  await openPage(IDS.study);
  await b.waitFor(`!document.getElementById('canvasWrap').hidden`);
  await instrument();
  await drawRel(0.3, 0.3, 0.7, 0.7);
  await b.eval(`document.getElementById('btnSubmit').click()`);
  await b.waitFor(`['기준 영역과 일치','일부 일치','차이 있음'].includes(document.getElementById('resultVerdict').textContent)`);
  const last = (await reviews()).at(-1);
  check("저장된 사용자 번호는 세션의 번호", last.user === IDS.other, JSON.stringify(last));

  // 9. 승인 없는 문제 / 무릎 아닌 문제 / 번호 없음 / 이메일 미인증
  await openPage(IDS.unapproved);
  check("승인 없는 문제: 안내만, 영상 없음", (await text("stageMessage")).includes("승인된 기준 영역이 없어") && (await hiddenEl("canvasWrap")) && (await disabled("btnSubmit")), await text("stageMessage"));
  await b.screenshot(join(OUT, "14_live_no_reference.png"));
  await openPage(IDS.brain);
  check("무릎이 아닌 문제: 채점할 수 없음 안내", (await text("stageMessage")).includes("채점할 수 있는 문제가 아닙니다"), await text("stageMessage"));
  await b.goto(`${BASE}/learn/knee`);
  await wait(300);
  check("문제 번호 없음 안내", (await text("stageMessage")).includes("문제 번호가 필요합니다"));
  await loginAs(IDS.user, false);
  await openPage(IDS.study);
  check("이메일 미인증: 인증 안내", (await text("stageMessage")).includes("이메일 인증"), await text("stageMessage"));

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
