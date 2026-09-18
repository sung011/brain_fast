/**
 * 홍보 팝업 수정 (/admin/popup/{idx})
 */
(function () {
    'use strict';

    function toLocalInput(value) {
        if (!value) return '';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return '';
        const pad = function (n) { return String(n).padStart(2, '0'); };
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
            'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    async function initPopupDetail(scope) {
        const root = (scope || document).querySelector('#popupDetailRoot') ||
            document.getElementById('popupDetailRoot');
        if (!root || root.dataset.bound === '1') return;
        root.dataset.bound = '1';

        const idx = root.getAttribute('data-idx');
        const form = root.querySelector('#popupEditForm');
        const preview = root.querySelector('#editPpPreview');
        if (!idx || !form) return;

        try {
            const res = await fetch('/admin/popups/' + idx, {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            const row = await res.json();
            if (!res.ok) throw new Error((row.detail && row.detail.message) || '조회 실패');
            form.pp_title.value = row.pp_title || '';
            form.pp_link.value = row.pp_link || '';
            form.pp_sort.value = row.pp_sort != null ? row.pp_sort : 0;
            form.state.value = row.state || 'N';
            form.start_at.value = toLocalInput(row.start_at);
            form.end_at.value = toLocalInput(row.end_at);
            const url = row.pp_image_url || row.pp_image;
            if (preview) {
                preview.innerHTML = url
                    ? ('<img src="' + String(url).replace(/"/g, '&quot;') +
                        '" alt="popup" style="max-width:100%;max-height:180px;object-fit:contain;">')
                    : '<span class="text-muted">이미지 없음</span>';
            }
        } catch (err) {
            if (preview) preview.innerHTML = '<span class="text-danger">불러오기 실패</span>';
            if (utils && utils.showError) utils.showError(String(err.message || err));
        }

        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            const btn = form.querySelector('#popupEditSubmitBtn');
            const resultBox = root.querySelector('#popupEditResult');
            const fd = new FormData(form);
            if (!form.file.files || !form.file.files[0]) {
                fd.delete('file');
            }
            if (btn) {
                btn.disabled = true;
                btn.textContent = '저장 중…';
            }
            try {
                const res = await fetch('/admin/popup/' + idx, {
                    method: 'PUT',
                    body: fd,
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    const msg = (data.detail && data.detail.message) || data.detail || '저장 실패';
                    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
                }
                if (resultBox) {
                    resultBox.classList.remove('d-none');
                    resultBox.textContent = '저장되었습니다.';
                }
                if (utils && utils.showAlert) utils.showAlert('팝업이 수정되었습니다.');
                if (data.pp_image && preview) {
                    const base = window.POPUP_NAS_BASE || '';
                    const path = data.pp_image.startsWith('/') ? data.pp_image : ('/' + data.pp_image);
                    const url = base ? (base + path) : path;
                    preview.innerHTML = '<img src="' + url.replace(/"/g, '&quot;') +
                        '" alt="popup" style="max-width:100%;max-height:180px;object-fit:contain;">';
                }
                form.file.value = '';
            } catch (err) {
                if (utils && utils.showError) utils.showError(String(err.message || err));
                else alert(String(err.message || err));
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = '저장';
                }
            }
        });
    }

    window.initPopupDetail = initPopupDetail;

    document.addEventListener('DOMContentLoaded', function () {
        if (document.getElementById('popupDetailRoot')) initPopupDetail(document);
    });
})();
