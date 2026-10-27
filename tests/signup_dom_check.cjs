'use strict';
/*
 * signup.js 를 브라우저 없이 Node 에서 실행해 화면 동작을 확인한다(가짜 DOM 사용).
 * 레이아웃·IME·Tab 순서는 확인하지 못한다. tests/test_signup_page.py 가 node 가 있을 때 이 파일을 실행한다.
 * 사용: node tests/signup_dom_check.cjs
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'assets/js/signup.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates/signup.html'), 'utf8');

function setup({ preview = false, desktopPointer = false } = {}) {
  const nodes = {};
  const timers = [];
  let active = null;

  function make(id, dataset = {}) {
    const classes = new Set();
    return {
      id, dataset, value: '', checked: false, disabled: false, hidden: false, required: false,
      type: 'password', textContent: '', attrs: {}, events: {}, open: false, maxLength: -1,
      classList: {
        toggle(name, on) { if (on === undefined ? !classes.has(name) : on) classes.add(name); else classes.delete(name); },
        contains: (name) => classes.has(name),
      },
      setAttribute(k, v) { this.attrs[k] = String(v); },
      removeAttribute(k) { delete this.attrs[k]; },
      focus() { active = this.id; },
      addEventListener(k, f) { (this.events[k] = this.events[k] || []).push(f); },
      fire(k) { for (const f of this.events[k] || []) f({ preventDefault() {} }); },
      showModal() { this.open = true; },
      close() { this.open = false; this.fire('close'); },
    };
  }

  for (const m of template.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
    nodes[m[1]] = make(m[1]);
    nodes[m[1]].hidden = /\shidden(?:\s|>)/.test(m[0]);
    const limit = m[0].match(/maxlength="(\d+)"/);
    if (limit) nodes[m[1]].maxLength = Number(limit[1]);
  }
  const previewOnly = ['stage3', 'heading3', 'scenario', 'mailAddress'];
  if (!preview) previewOnly.forEach((id) => delete nodes[id]);
  else delete nodes.demoNotice;
  if (nodes.scenario) nodes.scenario.value = 'normal';

  const consents = ['terms', 'privacy', 'age', 'marketing'].map((id) => nodes[id]);
  const rules = Object.fromEntries(['length', 'alpha', 'digit', 'special', 'space'].map((k) => [k, make(k)]));
  const body = { dataset: { mode: preview ? 'preview' : 'live', stage: '1' } };
  const doc = {
    body,
    getElementById: (id) => nodes[id] || null,
    querySelectorAll: (q) => (q === '.consent' ? consents : []),
    querySelector: (q) => rules[(q.match(/data-rule="([^"]+)"/) || [])[1]],
  };
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : ['2026-09-29T12:00:00'])); }
  }
  const win = { matchMedia: () => ({ matches: desktopPointer }) };
  vm.runInNewContext(source, {
    document: doc, window: win, Date: FixedDate,
    setTimeout(f) { timers.push(f); return timers.length; }, clearTimeout() {},
  });
  return { nodes, body, flush() { while (timers.length) timers.shift()(); }, active: () => active };
}

let passed = 0;
const failed = [];
function check(label, condition) {
  if (condition) { passed += 1; console.log('PASS ' + label); } else { failed.push(label); console.log('FAIL ' + label); }
}

function toProfileStage(s) {
  const n = s.nodes;
  n.email.value = 'learner@example.test';
  n.password.value = 'Example1!';
  n.confirm.value = 'Example1!';
  ['terms', 'privacy', 'age'].forEach((id) => { n[id].checked = true; });
  n.password.fire('input');
  n.accountForm.fire('submit');
  s.flush();
  n.nickname.value = 'sample';
  n.school.value = 'sample';
  n.birth.value = '2000-01-01';
  n.birth.fire('input');
}

for (const preview of [false, true]) {
  const tag = preview ? '[preview]' : '[live]';
  const s = setup({ preview });
  const n = s.nodes;
  toProfileStage(s);

  // ── 전공 select ──
  n.major.fire('blur');
  check(`${tag} 전공 미선택 후 blur → 오류·aria-invalid`, /선택/.test(n.majorError.textContent) && n.major.attrs['aria-invalid'] === 'true');
  n.major.value = '방사선학과';
  n.major.fire('change');
  check(`${tag} 전공 선택하면 오류 해제·가입 가능`, n.majorError.textContent === '' && !n.major.attrs['aria-invalid'] && !n.create.disabled);

  // ── 기타 전공 직접 입력 ──
  n.major.value = '기타 보건의료계열';
  n.major.fire('change');
  check(`${tag} 기타 선택 → 직접입력칸 표시·required·가입 불가`, !n.majorOther.hidden && n.majorOther.required === true && n.create.disabled);
  check(`${tag} 기타 선택 직후엔 오류를 바로 띄우지 않음`, n.majorError.textContent === '');

  n.majorOther.value = '   ';
  n.majorOther.fire('input');
  n.majorOther.fire('blur');
  check(`${tag} 공백만 → 오류 문구`, /직접|공백|입력/.test(n.majorError.textContent));
  check(`${tag} 공백만 → 직접입력칸 aria-invalid, select 는 아님`, n.majorOther.attrs['aria-invalid'] === 'true' && !n.major.attrs['aria-invalid']);
  check(`${tag} 공백만 → 가입 불가`, n.create.disabled);

  n.majorOther.value = '';
  n.majorOther.fire('input');
  n.majorOther.fire('blur');
  check(`${tag} 빈 값 blur → 오류·aria-invalid`, n.majorError.textContent !== '' && n.majorOther.attrs['aria-invalid'] === 'true');

  n.majorOther.value = '가'.repeat(21);
  n.majorOther.fire('input');
  check(`${tag} 21자 → 즉시 오류·가입 불가`, /20자/.test(n.majorError.textContent) && n.majorOther.attrs['aria-invalid'] === 'true' && n.create.disabled);

  n.majorOther.value = '가'.repeat(20);
  n.majorOther.fire('input');
  check(`${tag} 20자 → 오류 해제·가입 가능`, n.majorError.textContent === '' && !n.majorOther.attrs['aria-invalid'] && !n.create.disabled);

  n.majorOther.value = '   ';
  n.majorOther.fire('input');
  n.majorOther.fire('blur');
  n.major.value = '간호학과';
  n.major.fire('change');
  check(`${tag} 다른 전공으로 바꾸면 숨김·required 해제·오류 초기화`,
    n.majorOther.hidden && n.majorOther.required === false && n.majorError.textContent === '' && !n.majorOther.attrs['aria-invalid'] && !n.create.disabled);
}

// ── 단계에 따라 왼쪽 소개가 바뀜(body[data-stage]) ──
for (const preview of [false, true]) {
  const tag = preview ? '[preview]' : '[live]';
  const s = setup({ preview });
  check(`${tag} 처음엔 1단계 표시`, s.body.dataset.stage === '1');
  toProfileStage(s);
  check(`${tag} 기본 정보로 넘어가면 stage=2`, s.body.dataset.stage === '2');
  s.nodes.back.fire('click');
  check(`${tag} 이전으로 돌아오면 stage=1`, s.body.dataset.stage === '1');
}
{
  const s = setup({ preview: true });
  toProfileStage(s);
  s.nodes.nickname.value = 'sample';
  s.nodes.school.value = 'sample';
  s.nodes.birth.value = '2000-01-01';
  s.nodes.major.value = '방사선학과';
  s.nodes.birth.fire('input');
  s.nodes.profileForm.fire('submit');
  check('[preview] 가상 완료 화면은 stage=3', s.body.dataset.stage === '3');
}

// ── FINDING-004: 글자 수 한도에 닿으면 라벨 옆 '최대 N자'를 강조해 왜 멈췄는지 알림(높이 변화 없음) ──
{
  const s = setup({ preview: false });
  const n = s.nodes;
  const shown = (id) => n[id].classList.contains('show-limit');
  check('이메일 30자 한도가 템플릿에 있음', n.email.maxLength === 30);
  n.email.value = 'a'.repeat(29);
  n.email.fire('input');
  check('이메일 29자: 한도 표시 없음', !shown('emailLimit'));
  n.email.value = 'a'.repeat(30);
  n.email.fire('input');
  check('이메일 30자: 라벨 옆 한도 표시가 켜짐', shown('emailLimit'));
  n.email.value = 'a'.repeat(28);
  n.email.fire('input');
  check('이메일 다시 줄이면 한도 표시 꺼짐', !shown('emailLimit'));

  toProfileStage(s);
  n.nickname.value = '1234567890';
  n.nickname.fire('input');
  check('닉네임 10자: 라벨 옆 한도 표시가 켜짐', shown('nickLimit'));
  n.nickname.value = '123456789';
  n.nickname.fire('input');
  check('닉네임 9자: 한도 표시 꺼짐', !shown('nickLimit'));

  n.school.value = '가'.repeat(20);
  n.school.fire('input');
  check('학교 20자: 라벨 옆 한도 표시가 켜짐', shown('schoolLimit'));
  n.school.value = '가'.repeat(19);
  n.school.fire('input');
  check('학교 19자: 한도 표시 꺼짐', !shown('schoolLimit'));

  n.major.value = '기타 보건의료계열';
  n.major.fire('change');
  n.majorOther.value = '가'.repeat(20);
  n.majorOther.fire('input');
  check('기타 전공 20자: 라벨 옆 한도 표시가 켜짐', shown('majorLimit'));
  n.major.value = '간호학과';
  n.major.fire('change');
  check('다른 전공으로 바꾸면 기타 전공 한도 표시가 꺼짐', !shown('majorLimit'));
}

// ── 학교: 글자(한글·영문)만 ──
{
  const s = setup({ preview: false });
  const n = s.nodes;
  toProfileStage(s);
  n.major.value = '방사선학과';
  n.major.fire('change');
  const typeSchool = (value) => { n.school.value = value; n.school.fire('input'); n.school.fire('blur'); };

  for (const ok of ['서울대학교', 'Seoul National Univ.', '서울대학교 (글로벌캠퍼스)', '한국외대·글로벌', 'St. Mary-Hall']) {
    typeSchool(ok);
    check(`학교 허용: ${ok}`, n.schoolError.textContent === '' && !n.school.attrs['aria-invalid'] && !n.create.disabled);
  }
  for (const bad of ['12345', '서울고2', '2서울고', '서울대학교 3학년', '학교!', '학교@', '학교😀', '...', '()', '가나다_라']) {
    typeSchool(bad);
    check(`학교 거부: ${bad}`, n.schoolError.textContent !== '' && n.school.attrs['aria-invalid'] === 'true' && n.create.disabled);
  }
  typeSchool('서울고1');
  check('숫자 오류 문구가 이유를 알려 줌', /숫자/.test(n.schoolError.textContent));
  typeSchool('학교!');
  check('특수문자 오류 문구가 이유를 알려 줌', /특수문자/.test(n.schoolError.textContent));

  // 숫자는 입력하는 즉시(blur 전) 알린다.
  n.school.value = '서울고';
  n.school.fire('input');
  n.school.value = '서울고1';
  n.school.fire('input');
  check('숫자를 입력하는 즉시 오류 표시', n.schoolError.textContent !== '' && n.create.disabled);
  n.school.value = '서울고';
  n.school.fire('input');
  check('숫자를 지우면 오류 해제·가입 가능', n.schoolError.textContent === '' && !n.create.disabled);

  typeSchool('');
  check('빈 값은 기존대로 가입 불가', n.create.disabled);
  typeSchool('가'.repeat(20));
  check('한글 20자는 허용', n.schoolError.textContent === '' && !n.create.disabled);
  typeSchool('가'.repeat(21));
  check('21자는 거부', n.create.disabled);
}

// ── 템플릿 접근성 연결 ──
const otherTag = template.match(/<input[^>]*id="majorOther"[^>]*>/)[0];
const majorTag = template.match(/<select[^>]*id="major"[^>]*>/)[0];
check('직접입력칸 aria-describedby 에 majorHint·majorError', /aria-describedby="[^"]*majorHint[^"]*majorError|aria-describedby="[^"]*majorError[^"]*majorHint/.test(otherTag));
check('전공 select aria-describedby 에 majorError', /aria-describedby="[^"]*majorError/.test(majorTag));

// ── 첫 이메일 포커스 (SIGNUP-003) ──
check('데스크톱(마우스) 첫 진입 시 이메일 칸 포커스', setup({ desktopPointer: true }).active() === 'email');
check('터치 기기는 키보드가 자동으로 열리지 않게 포커스하지 않음', setup({ desktopPointer: false }).active() === null);

// ── 운영 화면의 고지·완료 DOM (SIGNUP-001) ──
const live = setup({ preview: false });
check('운영 화면에 상시 고지 요소가 있고 처음부터 보임', live.nodes.demoNotice && live.nodes.demoNotice.hidden === false);
check('운영 화면에 가상 완료 화면(stage3)이 없음', !live.nodes.stage3);
const pv = setup({ preview: true });
check('미리보기에는 상시 고지 대신 설계 도구 막대 사용', !pv.nodes.demoNotice && !!pv.nodes.scenario);

console.log(`SUMMARY ${passed} passed, ${failed.length} failed`);
assert.strictEqual(failed.length, 0, `실패한 확인: ${failed.join(' / ')}`);
