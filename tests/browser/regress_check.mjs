// 기존 화면(메인 뇌 분석·회원가입·관리자 로그인) 회귀 확인. 실행 중인 앱 서버가 필요하다: node tests/browser/regress_check.mjs http://127.0.0.1:8000
import { launch, wait } from "./cdp.mjs";
const BASE = process.argv[2] || "http://127.0.0.1:8011";
let failures = 0;
const check = (n, c, d = "") => { console.log(`${c ? "OK " : "!! "}${n}${d ? "  " + d : ""}`); if (!c) failures++; };
const b = await launch({ width: 1280, height: 900, port: 9555 });
try {
  await b.goto(`${BASE}/`);
  await wait(500);
  check("메인(뇌 화면 공유 분석) 열림", (await b.eval(`document.title`)).length > 0 && (await b.eval(`!!document.getElementById('btnShare') && !!document.getElementById('btnAnalyze')`)));
  check("메인: 부위 기본값은 뇌 CT", (await b.eval(`document.getElementById('partSelect').value`)) === "brain");
  await b.goto(`${BASE}/signup`);
  await wait(500);
  check("회원가입 화면 열림(실제 모드)", await b.eval(`document.body.dataset.mode === 'live' && !!document.querySelector('main')`));
  await b.goto(`${BASE}/signup?preview=1`);
  await wait(500);
  check("회원가입 화면 열림(미리보기)", await b.eval(`document.body.dataset.mode === 'preview'`));
  await b.eval(`document.querySelector('[data-preview="2"]').click()`);
  await wait(300);
  check("회원가입 미리보기: 단계 이동 동작", (await b.eval(`document.body.dataset.stage`)) === "2");
  await b.goto(`${BASE}/admin/login`);
  await wait(400);
  check("관리자 로그인 화면 열림", await b.eval(`document.body.innerText.length > 10`));
  const errors = b.events.filter((e) => e.method === "Runtime.exceptionThrown" || (e.method === "Runtime.consoleAPICalled" && e.params.type === "error"));
  check("콘솔 오류 없음", errors.length === 0, errors.length ? JSON.stringify(errors[0].params).slice(0, 250) : "");
} catch (e) { failures++; console.log("!! 예외:", e.message); } finally { await b.close(); }
console.log(failures === 0 ? "모두 통과" : `실패 ${failures}건`);
