(function () {
    'use strict';

    const form = document.forms['login'];

    document.addEventListener('DOMContentLoaded', () => {
        eventListeners();
    });

    function eventListeners() {
        form.addEventListener('submit', handleSubmit)
    }

    async function handleSubmit(e) {
        e.preventDefault();

        const formElement = e.target;

        if (formElement.userId.value === '') {
            return utils.showToast('아이디를 입력해주세요.', formElement.userId.focus());
        }

        if (formElement.userPw.value === '') {
            return utils.showToast('비밀번호를 입력해주세요.', formElement.userPw.focus());
        }

        // 서버 UserLogin 스키마(user_id, user_pw)에 맞춰 JSON으로 전송
        const body = {
            user_id: formElement.userId.value,
            user_pw: formElement.userPw.value,
        };

        const response = await API.fetchData('/admin/login', body);
        console.log(response);
        if (response.ok) {
            if (parseInt(response.mb_level, 10) === 10) {
                location.href = baseUrl + '/admin';
            } else {
                utils.showAlert('관한이 없습니다', () => location.reload());
            }
        } else {
            utils.showError(response.message, '�좎껌�� �ㅽ뙣�덉뒿�덈떎.');
        }
    }
})();