/* 학습자 계정찾기. 같은 서버의 API를 호출하며 비밀번호는 저장하지 않는다. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const tabs = [$('idTab'), $('pwTab')];
  let verifiedIdentity = null;
  let flowVersion = 0;

  function message(id, text, success = false) {
    const element = $(id);
    element.textContent = text;
    element.hidden = !text;
    element.classList.toggle('success', success);
  }

  function clearVerification() {
    flowVersion += 1;
    verifiedIdentity = null;
    $('identityFields').disabled = false;
    $('verifyButton').hidden = false;
    $('resetSection').hidden = true;
    $('resetForm').reset();
    $('resetButton').disabled = false;
    $('newPassword').type = $('confirmPassword').type = 'password';
    $('passwordMatch').textContent = '';
    $('newPassword').setCustomValidity('');
    $('confirmPassword').setCustomValidity('');
    message('verifyStatus', '');
    message('resetStatus', '');
  }

  function switchTab(index, focus = false) {
    clearVerification();
    $('idResult').hidden = true;
    message('idStatus', '');
    tabs.forEach((tab, i) => {
      tab.setAttribute('aria-selected', String(i === index));
      tab.tabIndex = i === index ? 0 : -1;
      $(tab.getAttribute('aria-controls')).hidden = i !== index;
    });
    if (focus) tabs[index].focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => switchTab(index));
    tab.addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        switchTab(event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index, true);
      }
    });
  });

  ['findIdBirth', 'findPwBirth'].forEach(id => {
    $(id).addEventListener('input', event => {
      const digits = event.target.value.replace(/[^0-9]/g, '').slice(0, 8);
      event.target.value = digits.length > 6 ? `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6)}`
        : digits.length > 4 ? `${digits.slice(0, 4)}-${digits.slice(4)}` : digits;
      event.target.setCustomValidity('');
    });
  });
  ['findIdName', 'findIdBirth'].forEach(id => $(id).addEventListener('input', () => {
    flowVersion += 1;
    $('idResult').hidden = true;
    message('idStatus', '');
  }));
  ['findPwId', 'findPwName', 'findPwBirth'].forEach(id => $(id).addEventListener('input', clearVerification));

  function validBirth(id) {
    const input = $(id), value = input.value;
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    let valid = false;
    if (match) {
      const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
      const date = new Date(`${value}T00:00:00`), now = new Date();
      valid = date.getFullYear() === year && date.getMonth() + 1 === month && date.getDate() === day && date <= now;
    }
    input.setCustomValidity(valid ? '' : '올바른 생년월일을 YYYY-MM-DD 형식으로 입력해 주세요.');
    if (!valid) input.reportValidity();
    return valid;
  }

  async function post(url, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body), signal: controller.signal, cache: 'no-store'});
      let data;
      try { data = await response.json(); } catch { throw new Error('서버 응답을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.'); }
      if (!response.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : response.status === 422
          ? '입력 형식과 비밀번호 길이를 확인해 주세요.' : '요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      }
      if (data.ok !== true) throw new Error('요청을 처리하지 못했습니다.');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.');
      if (error instanceof TypeError) throw new Error('서버에 연결할 수 없습니다. 연결 상태를 확인해 주세요.');
      throw error;
    } finally { clearTimeout(timeout); }
  }

  $('findIdForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!validBirth('findIdBirth')) return;
    const button = event.submitter || event.target.querySelector('button');
    button.disabled = true;
    const version = flowVersion;
    $('idResult').hidden = true;
    message('idStatus', '확인 중입니다.', true);
    try {
      const data = await post('/find-id', {user_name: $('findIdName').value.trim(), user_birth: $('findIdBirth').value});
      if (version !== flowVersion) return;
      const ids = data.user_ids || [data.user_id];
      // 서버는 일부를 가린 아이디(예: jo***@gmail.com)만 돌려준다.
      $('foundIds').replaceChildren(...ids.map(id => { const li = document.createElement('li'); li.textContent = id; return li; }));
      $('idResult').hidden = false;
      message('idStatus', '아이디를 찾았습니다. 일부는 가려서 표시합니다.', true);
    } catch (error) { if (version === flowVersion) message('idStatus', error.message); }
    finally { button.disabled = false; }
  });

  $('useFoundId').addEventListener('click', () => {
    // 가려진 아이디는 그대로 쓸 수 없으므로 이름·생년월일만 옮기고 아이디는 직접 입력한다.
    $('findPwId').value = '';
    $('findPwName').value = $('findIdName').value;
    $('findPwBirth').value = $('findIdBirth').value;
    switchTab(1);
    $('findPwId').focus();
  });

  $('verifyForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!validBirth('findPwBirth')) return;
    const identity = {user_id: $('findPwId').value.trim(), user_name: $('findPwName').value.trim(), user_birth: $('findPwBirth').value};
    $('verifyButton').disabled = true;
    const version = flowVersion;
    message('verifyStatus', '확인 중입니다.', true);
    try {
      await post('/find-pw/verify', identity);
      if (version !== flowVersion) return;
      verifiedIdentity = identity;
      $('identityFields').disabled = true;
      $('verifyButton').hidden = true;
      $('resetSection').hidden = false;
      message('verifyStatus', '가입 정보가 확인되었습니다. 새 비밀번호를 입력해 주세요.', true);
      $('newPassword').focus();
    } catch (error) { if (version === flowVersion) { clearVerification(); message('verifyStatus', error.message); } }
    finally { $('verifyButton').disabled = false; }
  });
  $('editIdentity').addEventListener('click', () => { clearVerification(); $('findPwId').focus(); });

  function passwordMatch() {
    const first = $('newPassword').value, second = $('confirmPassword').value;
    const mismatch = !!second && first !== second;
    $('confirmPassword').setCustomValidity(mismatch ? '새 비밀번호와 확인 비밀번호가 일치하지 않습니다.' : '');
    $('passwordMatch').textContent = !second ? '' : mismatch ? '비밀번호가 일치하지 않습니다.' : '비밀번호가 일치합니다.';
    $('passwordMatch').className = mismatch ? 'match-error' : 'match-ok';
    $('newPassword').setCustomValidity(new TextEncoder().encode(first).length > 72 ? '비밀번호는 UTF-8 기준 72바이트 이내로 입력해 주세요.' : '');
  }
  ['newPassword', 'confirmPassword'].forEach(id => $(id).addEventListener('input', passwordMatch));
  $('showPassword').addEventListener('change', event => {
    $('newPassword').type = $('confirmPassword').type = event.target.checked ? 'text' : 'password';
  });
  $('resetForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!verifiedIdentity) { clearVerification(); message('verifyStatus', '가입 정보를 먼저 확인해 주세요.'); return; }
    passwordMatch();
    if (!event.target.reportValidity()) return;
    $('resetButton').disabled = true;
    const version = flowVersion;
    message('resetStatus', '변경 중입니다.', true);
    try {
      const data = await post('/find-pw', {...verifiedIdentity, user_pw: $('newPassword').value, user_pw_confirm: $('confirmPassword').value});
      if (version !== flowVersion) return;
      clearVerification();
      message('verifyStatus', `${data.message} 새 비밀번호로 로그인해 주세요.`, true);
    } catch (error) { if (version === flowVersion) message('resetStatus', error.message); }
    finally { $('resetButton').disabled = false; }
  });
})();
