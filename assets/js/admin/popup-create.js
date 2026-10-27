/**
 * 홍보 팝업 등록 (/admin/popup/create)
 */
(function () {
    'use strict';

    async function fillNextSort(form) {
        const sortInput = form.pp_sort;
        if (!sortInput) return;
        if (!form.state || form.state.value !== 'N') {
            sortInput.readOnly = false;
            return;
        }
        sortInput.readOnly = true;
        try {
            const res = await fetch('/admin/popups/next-sort', {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            const data = await res.json().catch(function () { return {}; });
            if (res.ok && data && data.pp_sort != null && form.state.value === 'N') {
                sortInput.value = String(data.pp_sort);
            }
        } catch (error) {
            // 조회에 실패해도 등록 시 서버가 노출 다음 번호를 다시 계산한다.
        }
    }

    function refreshOpenCreateSorts() {
        document.querySelectorAll('#popupCreateForm').forEach(function (form) {
            if (form.state && form.state.value === 'N') fillNextSort(form);
        });
    }

    function bindSortRefresh() {
        if (window.__popupCreateSortSSEBound) return;
        window.__popupCreateSortSSEBound = true;
        if (window.AdminPopupSSE) {
            AdminPopupSSE.on('popup_created', refreshOpenCreateSorts);
            AdminPopupSSE.on('popup_updated', refreshOpenCreateSorts);
            AdminPopupSSE.on('popup_deleted', refreshOpenCreateSorts);
        }
    }

    function initPopupCreate(scope) {
        const root = scope || document;
        const form = root.querySelector('#popupCreateForm') || document.forms['popupCreate'];
        if (!form || form.dataset.bound === '1') return;
        form.dataset.bound = '1';
        bindSortRefresh();
        fillNextSort(form);

        if (form.state) {
            form.state.addEventListener('change', function () {
                if (form.state.value === 'N') {
                    fillNextSort(form);
                    return;
                }
                form.pp_sort.readOnly = false;
                form.pp_sort.value = '0';
            });
        }

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
                form.state.value = 'N';
                fillNextSort(form);
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
