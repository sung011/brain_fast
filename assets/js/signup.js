/*
 * 회원가입 화면 동작.
 *
 * 서버로 아무것도 보내지 않는다(fetch·XHR·form 전송 없음). 입력값은 이 화면의 메모리에만 있고
 * console·localStorage·sessionStorage·URL 어디에도 남기지 않는다.
 * 실제 가입 API가 아직 없어서, 마지막 "회원가입"은 준비 중 안내만 보여 준다.
 * body[data-mode="preview"] (?preview=1)일 때만 단계 이동, 오류 시뮬레이션, 가상 완료 화면이 켜진다.
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const PREVIEW = document.body.dataset.mode === 'preview';

  const LIMITS = { email: 30, nickname: 10, school: 20, major: 20, pwMin: 8, pwMax: 64, minAge: 14 };
  const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const OTHER_MAJOR = '기타 보건의료계열';

  let stage = 1;
  let busy = false;
  let toastTimer = null;
  let lastTrigger = null;

  const charCount = (text) => [...text].length;

  // ── 알림 ──
  function toast(text) {
    const box = $('toast');
    box.textContent = text;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 4000);
  }

  function setInvalid(inputId, invalid) {
    const input = $(inputId);
    if (invalid) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
  }

  function setError(inputId, errorId, message) {
    $(errorId).textContent = message;
    setInvalid(inputId, Boolean(message));
  }

  // 입력칸은 maxlength 에서 조용히 입력을 멈춘다. 압축 화면은 힌트 문장을 숨기므로 라벨 옆에 "최대 N자"를 항상 보여 주고,
  // 한도에 닿은 순간 그 글씨를 강조(CSS)해 멈춘 이유를 알린다. 줄을 새로 끼우지 않아 화면 높이는 변하지 않는다.
  const LIMIT_MARKS = { email: 'emailLimit', nickname: 'nickLimit', school: 'schoolLimit', majorOther: 'majorLimit' };

  function updateLimitHint(id) {
    const input = $(id);
    const atLimit = !input.hidden && input.maxLength > 0 && input.value.length >= input.maxLength;
    $(LIMIT_MARKS[id]).classList.toggle('show-limit', atLimit);
  }

  // ── 검사 함수 ──
  function passwordRules() {
    const p = $('password').value;
    return {
      length: p.length >= LIMITS.pwMin && p.length <= LIMITS.pwMax,
      alpha: /[a-zA-Z]/.test(p),
      digit: /[0-9]/.test(p),
      special: /[!-/:-@[-`{-~]/.test(p),
      space: p.length > 0 && !/\s/.test(p),
    };
  }

  function emailProblem() {
    const value = $('email').value;
    if (!value) return '이메일을 입력해 주세요.';
    if (value.length > LIMITS.email) return `이메일은 ${LIMITS.email}자 이하로 입력해 주세요.`;
    if (!EMAIL_PATTERN.test(value)) return '이메일 형식이 올바르지 않습니다. 예: example@email.com';
    return '';
  }

  function textProblem(id, label, max) {
    const length = charCount($(id).value.trim());
    if (length < 1) return `${label}을(를) 입력해 주세요. 공백만 입력할 수 없습니다.`;
    if (length > max) return `${label}은(는) ${max}자 이하로 입력해 주세요.`;
    return '';
  }

  // 학교명은 글자(한글·영문)만 받는다. 공백과 . · ( ) - 는 "서울대학교 (글로벌캠퍼스)" 같은 이름을 위해 허용한다.
  const SCHOOL_PATTERN = /^[A-Za-z가-힣\s.·()-]+$/;

  function schoolProblem() {
    const base = textProblem('school', '학교', LIMITS.school);
    if (base) return base;
    const value = $('school').value.trim();
    if (/[0-9]/.test(value)) return '학교명에는 숫자를 쓸 수 없습니다. 한글이나 영문으로 입력해 주세요.';
    if (!SCHOOL_PATTERN.test(value) || !/[A-Za-z가-힣]/.test(value)) {
      return '학교명은 한글·영문으로 입력해 주세요. 특수문자와 이모지는 쓸 수 없습니다.';
    }
    return '';
  }

  function birthProblem() {
    const value = $('birth').value;
    if (!value) return '생년월일을 입력해 주세요.';
    const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!parts) return '올바른 생년월일을 입력해 주세요.';
    const [y, m, d] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
    const date = new Date(Date.UTC(y, m - 1, d));
    if (date.getUTCFullYear() !== y || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d) {
      return '존재하지 않는 날짜입니다.';
    }
    const now = new Date();
    if (date.getTime() > Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) {
      return '미래 날짜는 입력할 수 없습니다.';
    }
    let age = now.getFullYear() - y;
    if (now.getMonth() + 1 < m || (now.getMonth() + 1 === m && now.getDate() < d)) age -= 1;
    if (age < LIMITS.minAge) return `만 ${LIMITS.minAge}세 이상 가입할 수 있습니다.`;
    return '';
  }

  function majorProblem() {
    if (!$('major').value) return '전공을 선택해 주세요.';
    if ($('major').value === OTHER_MAJOR) return textProblem('majorOther', '전공', LIMITS.major);
    return '';
  }

  const accountValid = () =>
    !emailProblem() &&
    Object.values(passwordRules()).every(Boolean) &&
    $('password').value === $('confirm').value &&
    ['terms', 'privacy', 'age'].every((id) => $(id).checked);

  const profileValid = () =>
    accountValid() &&
    !textProblem('nickname', '닉네임', LIMITS.nickname) &&
    !schoolProblem() &&
    !birthProblem() &&
    !majorProblem();

  // ── 화면 갱신 ──
  function update() {
    const rules = passwordRules();
    Object.entries(rules).forEach(([key, met]) => {
      document.querySelector(`[data-rule="${key}"]`).classList.toggle('met', met);
    });

    const mismatch = $('confirm').value !== '' && $('password').value !== $('confirm').value;
    setError('confirm', 'confirmError', mismatch ? '비밀번호가 일치하지 않습니다.' : '');

    $('next').disabled = busy || !accountValid();
    $('create').disabled = busy || !profileValid();
  }

  function show(n, focus = true) {
    stage = n;
    document.body.dataset.stage = String(n); // CSS 가 단계에 따라 왼쪽 소개(그림 ↔ 서비스 항목)를 바꾼다
    $('stage1').hidden = n !== 1;
    $('stage2').hidden = n !== 2;
    if ($('stage3')) $('stage3').hidden = n !== 3;
    $('steps').hidden = n === 3;
    $('stepOne').classList.toggle('current', n === 1);
    $('stepTwo').classList.toggle('current', n === 2);
    document.querySelectorAll('[data-preview]').forEach((b) => {
      b.setAttribute('aria-pressed', String(Number(b.dataset.preview) === n));
    });
    if ($('mailAddress')) $('mailAddress').textContent = $('email').value || 'learner@example.test';
    if (focus) {
      if (n === 1) $('email').focus();
      else $(`heading${n}`).focus();
    }
    update();
  }

  function setBusy(value) {
    busy = value;
    update();
  }

  // ── 이벤트: 표시 토글, 로그인, 약관 ──
  document.querySelectorAll('[data-show]').forEach((button) => {
    button.addEventListener('click', () => {
      const field = $(button.dataset.show);
      const reveal = field.type === 'password';
      field.type = reveal ? 'text' : 'password';
      button.textContent = reveal ? '숨김' : '표시';
      button.setAttribute('aria-pressed', String(reveal));
      const name = button.dataset.show === 'confirm' ? '비밀번호 확인' : '비밀번호';
      button.setAttribute('aria-label', `${name} ${reveal ? '숨김' : '표시'}`);
    });
  });

  document.querySelectorAll('[data-login]').forEach((button) => {
    button.addEventListener('click', () => toast('로그인 화면은 후속 작업에서 연결합니다.'));
  });

  document.querySelectorAll('[data-terms]').forEach((button) => {
    button.addEventListener('click', () => {
      lastTrigger = button;
      $('termsTitle').textContent = button.dataset.terms;
      $('termsDialog').showModal();
    });
  });
  $('closeTerms').addEventListener('click', () => $('termsDialog').close());
  $('termsDialog').addEventListener('close', () => {
    if (lastTrigger) lastTrigger.focus();
  });

  // ── 이벤트: 입력 ──
  $('email').addEventListener('input', () => {
    $('email').value = $('email').value.replace(/\s/g, '');
    setError('email', 'emailError', '');
    updateLimitHint('email');
    update();
  });
  $('email').addEventListener('blur', () => {
    if ($('email').value) setError('email', 'emailError', emailProblem());
  });

  ['password', 'confirm'].forEach((id) => $(id).addEventListener('input', update));

  // instant: 입력하는 즉시 오류를 보여 줄 조건. 없으면 칸을 벗어날 때(blur)만 보여 준다.
  const textFields = [
    { id: 'nickname', errorId: 'nickError', problem: () => textProblem('nickname', '닉네임', LIMITS.nickname) },
    { id: 'school', errorId: 'schoolError', problem: schoolProblem, instant: () => /[0-9]/.test($('school').value) },
  ];
  textFields.forEach(({ id, errorId, problem, instant }) => {
    $(id).addEventListener('input', () => {
      setError(id, errorId, instant && instant() ? problem() : '');
      updateLimitHint(id);
      update();
    });
    $(id).addEventListener('blur', () => {
      if ($(id).value !== '') setError(id, errorId, problem());
    });
  });

  $('birth').addEventListener('input', () => {
    setError('birth', 'birthError', $('birth').value ? birthProblem() : '');
    update();
  });

  // 전공 오류는 한 자리(majorError)에 보이고, 지금 화면에 있는 칸(select 또는 직접 입력칸)에만 aria-invalid 를 건다.
  function showMajorError() {
    const other = $('major').value === OTHER_MAJOR;
    const message = majorProblem();
    $('majorError').textContent = message;
    setInvalid('major', !other && Boolean(message));
    setInvalid('majorOther', other && Boolean(message));
  }

  function clearMajorError() {
    $('majorError').textContent = '';
    setInvalid('major', false);
    setInvalid('majorOther', false);
  }

  $('major').addEventListener('blur', showMajorError);
  $('major').addEventListener('change', () => {
    const other = $('major').value === OTHER_MAJOR;
    $('majorOther').hidden = !other;
    $('majorOther').required = other;
    clearMajorError();
    updateLimitHint('majorOther');
    if (other) $('majorOther').focus();
    update();
  });

  // 직접 입력은 입력 중에는 길이 초과만 바로 알리고, 칸을 벗어날 때(blur) 빈 값·공백도 알린다.
  $('majorOther').addEventListener('input', () => {
    if (charCount($('majorOther').value.trim()) > LIMITS.major) showMajorError();
    else clearMajorError();
    updateLimitHint('majorOther');
    update();
  });
  $('majorOther').addEventListener('blur', showMajorError);

  // ── 이벤트: 동의 ──
  const consents = () => [...document.querySelectorAll('.consent')];
  $('agreeAll').addEventListener('change', () => {
    $('agreeAll').indeterminate = false;
    consents().forEach((c) => { c.checked = $('agreeAll').checked; });
    update();
  });
  consents().forEach((c) => {
    c.addEventListener('change', () => {
      const all = consents();
      $('agreeAll').checked = all.every((item) => item.checked);
      $('agreeAll').indeterminate = all.some((item) => item.checked) && !$('agreeAll').checked;
      update();
    });
  });

  // ── 이벤트: 단계 이동·제출 ──
  $('back').addEventListener('click', () => show(1));

  $('accountForm').addEventListener('submit', (event) => {
    event.preventDefault();
    if ($('next').disabled) return;

    // 실제 이메일 조회 API가 없으므로 운영 모드에서는 검사 없이 다음 단계로 간다.
    if (!PREVIEW) { show(2); return; }

    const requested = $('email').value;
    const button = $('next');
    setBusy(true);
    button.textContent = '확인 중…';
    setTimeout(() => {
      setBusy(false);
      button.innerHTML = '다음 단계 <span aria-hidden="true">→</span>';
      if (requested !== $('email').value) return; // 그 사이 이메일을 고쳤으면 이전 결과는 버린다
      const scenario = $('scenario').value;
      if (scenario === 'email') {
        setError('email', 'emailError', '이미 가입된 이메일입니다.');
        $('email').focus();
      } else if (scenario === 'network') {
        $('emailError').textContent = '이메일을 확인하지 못했습니다. 다시 시도해 주세요.';
      } else {
        show(2);
      }
    }, 500);
  });

  $('profileForm').addEventListener('submit', (event) => {
    event.preventDefault();
    if ($('create').disabled) return;
    $('submitError').textContent = '';
    $('liveNotice').hidden = true;

    if (!PREVIEW) {
      $('liveNotice').textContent =
        '회원가입 연결은 아직 준비되지 않았습니다. 입력한 정보는 전송되거나 저장되지 않았습니다.';
      $('liveNotice').hidden = false;
      return;
    }
    if ($('scenario').value === 'network') {
      $('submitError').textContent = '가입 요청을 완료하지 못했습니다. 입력은 유지됩니다. 다시 시도해 주세요.';
      return;
    }
    show(3);
    $('password').value = '';
    $('confirm').value = '';
    update();
  });

  // ── 설계 미리보기 도구 ──
  if (PREVIEW) {
    document.querySelectorAll('[data-preview]').forEach((b) => {
      b.addEventListener('click', () => show(Number(b.dataset.preview)));
    });
    $('scenario').addEventListener('change', () => {
      $('submitError').textContent = '';
      $('emailError').textContent = '';
      update();
    });
  }

  update();

  // 마우스 기기에서는 첫 입력칸에 바로 포커스한다. 터치 기기는 키보드가 자동으로 열려 소개를 가리므로 하지 않는다.
  // preventScroll: 창이 좁을 때 이메일 칸으로 자동 스크롤되어 맨 위의 "실제 가입 아님" 안내가 가려지지 않게 한다.
  if (typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    $('email').focus({ preventScroll: true });
  }
})();
