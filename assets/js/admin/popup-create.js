/**
 * 홍보 팝업 등록 (/admin/popup/create)
 */
(function () {
    'use strict';

    function initPopupCreate(scope) {
        const root = scope || document;
        const form = root.querySelector('#popupCreateForm') || document.forms['popupCreate'];
        if (!form || form.dataset.bound === '1') return;
        form.dataset.bound = '1';

        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            const btn = form.querySelector('#popupCreateSubmitBtn');
            const resultBox = root.querySelector('#popupCreateResult');
            if (!form.file.files || !form.file.files[0]) {
                if (utils && utils.showToast) utils.showToast('이미지를 선택해주세요.', form.file.focus());
                return;
            }
            const fd = new FormData(form);
            if (btn) {
                btn.disabled = true;
                btn.textContent = '업로드 중…';
            }
            try {
                const res = await fetch('/admin/popup', {
                    method: 'POST',
                    body: fd,
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    const msg = (data.detail && data.detail.message) || data.detail || '등록 실패';
                    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
                }
                if (resultBox) {
                    resultBox.classList.remove('d-none');
                    resultBox.textContent = '등록 완료 (#' + data.idx + ')';
                }
                if (utils && utils.showAlert) utils.showAlert('팝업이 등록되었습니다.');
                form.reset();
                form.pp_sort.value = '0';
                form.state.value = 'N';
            } catch (err) {
                if (utils && utils.showError) utils.showError(String(err.message || err));
                else alert(String(err.message || err));
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="me-1" data-feather="upload"></i> NAS 업로드 후 등록';
                    if (window.feather) feather.replace();
                }
            }
        });
    }

    window.initPopupCreate = initPopupCreate;

    document.addEventListener('DOMContentLoaded', function () {
        if (document.getElementById('popupCreateForm')) initPopupCreate(document);
    });
})();
