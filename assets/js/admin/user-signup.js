/**
 * 관리자 회원가입 화면 스크립트.
 *
 * 흐름:
 * 1) 이메일(아이디) 칸에서 포커스를 빼면 GET /admin/user/check-id 로 중복 확인
 * 2) [가입하기] 누르면 필수값·비밀번호 확인 후 POST /admin/user/signup
 * 3) 성공하면 폼을 비우고, 회원 목록은 SSE로 자동 갱신된다
 *
 * 탭으로 HTML만 다시 불러올 때도 동작해야 해서 IIFE + signupBound 플래그를 쓴다.
 */
(function () {
    'use strict';

    // name="userSignup" 인 폼. 탭에 여러 번 삽입돼도 현재 문서의 그 폼만 잡는다.
    const form = document.forms['userSignup'];
    let lastCheckedUserId = '';   // 마지막으로 서버에 물어본 아이디
    let userIdAvailable = null;   // true=사용 가능, false=중복, null=아직 확인 안 함

    document.addEventListener('DOMContentLoaded', () => {
        eventListeners();
    });

    // 탭 fetch로 나중에 삽입되면 DOMContentLoaded가 이미 지난 상태라 바로 바인딩한다.
    if (document.readyState !== 'loading') {
        eventListeners();
    }

    function eventListeners() {
        // 같은 스크립트가 두 번 실행되면 submit이 중복되므로 한 번만 붙인다.
        if (!form || form.dataset.signupBound === '1') {
            return;
        }
        form.dataset.signupBound = '1';
        form.addEventListener('submit', handleSubmit);
        form.userId.addEventListener('input', handleUserIdInput);
        form.userId.addEventListener('blur', handleUserIdBlur);
    }

    // 아이디를 다시 치면 이전 중복검사 결과는 버린다.
    function handleUserIdInput() {
        lastCheckedUserId = '';
        userIdAvailable = null;
    }

    // 아이디 칸을 벗어나면 바로 중복 여부를 알려 준다.
    async function handleUserIdBlur() {
        if (form.userId.value.trim() === '') {
            return;
        }
        await checkUserIdAvailability(form.userId, true);
    }

    /**
     * GET /admin/user/check-id?user_id=...
     * @param {HTMLInputElement} userIdInput
     * @param {boolean} showMessageIfTaken 중복이면 토스트를 띄울지
     * @returns {Promise<boolean>} 사용 가능하면 true
     */
    async function checkUserIdAvailability(userIdInput, showMessageIfTaken) {
        const userId = userIdInput.value.trim();
        if (userId === '') {
            userIdAvailable = null;
            return false;
        }

        // 같은 아이디를 이미 확인했으면 서버를 다시 부르지 않는다.
        if (userId === lastCheckedUserId && userIdAvailable !== null) {
            if (!userIdAvailable && showMessageIfTaken) {
                utils.showToast('이미 사용 중인 아이디입니다.', userIdInput.focus());
            }
            return userIdAvailable;
        }

        try {
            const url = (typeof baseUrl === 'string' ? baseUrl : '') +
                '/admin/user/check-id?user_id=' + encodeURIComponent(userId);
            const response = await fetch(url, {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            const data = await response.json();

            lastCheckedUserId = userId;
            userIdAvailable = data.available === true;

            if (!userIdAvailable && showMessageIfTaken) {
                utils.showToast(data.message || '이미 사용 중인 아이디입니다.', userIdInput.focus());
            }

            return userIdAvailable;
        } catch (error) {
            utils.showToast('아이디 확인에 실패했습니다.');
            return false;
        }
    }

    function resetSignupForm(formElement) {
        formElement.reset();
        lastCheckedUserId = '';
        userIdAvailable = null;
    }

    // 가입하기: 화면 검증 → 중복 재확인 → POST /admin/user/signup
    async function handleSubmit(e) {
        e.preventDefault();

        const formElement = e.target;

        if (formElement.userId.value === '') {
            return utils.showToast('이메일(아이디)를 입력해주세요.', formElement.userId.focus());
        }

        const isAvailable = await checkUserIdAvailability(formElement.userId, true);
        if (!isAvailable) {
            return;
        }

        if (formElement.userName.value === '') {
            return utils.showToast('이름(닉네임)을 입력해주세요.', formElement.userName.focus());
        }

        if (formElement.userBirth.value === '') {
            return utils.showToast('생년월일을 선택해주세요.', formElement.userBirth.focus());
        }

        if (formElement.userUn.value === '') {
            return utils.showToast('학교를 입력해주세요.', formElement.userUn.focus());
        }

        if (formElement.userSp.value === '') {
            return utils.showToast('전공을 입력해주세요.', formElement.userSp.focus());
        }

        if (formElement.userPw.value === '') {
            return utils.showToast('비밀번호를 입력해주세요.', formElement.userPw.focus());
        }

        if (formElement.userPwConfirm.value === '') {
            return utils.showToast('비밀번호 확인을 입력해주세요.', formElement.userPwConfirm.focus());
        }

        if (formElement.userPw.value.length < 4) {
            return utils.showToast('비밀번호는 4자 이상이어야 합니다.', formElement.userPw.focus());
        }

        if (formElement.userPw.value !== formElement.userPwConfirm.value) {
            return utils.showToast('비밀번호가 일치하지 않습니다.', formElement.userPwConfirm.focus());
        }

        // 서버 UserCreate 스키마와 같은 키 이름
        const body = {
            user_id: formElement.userId.value.trim(),
            user_name: formElement.userName.value,
            user_birth: formElement.userBirth.value,
            user_un: formElement.userUn.value,
            user_sp: formElement.userSp.value,
            user_pw: formElement.userPw.value,
        };

        const response = await API.fetchData('/admin/user/signup', body);
        if (response.ok) {
            resetSignupForm(formElement);
            utils.showAlert('회원가입이 완료되었습니다.');
            return;
        }

        utils.showError(response.message);
    }
})();
