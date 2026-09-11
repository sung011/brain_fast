/**
 * 학습 등록 폼 — NAS 업로드 + study 테이블 저장
 * scope(탭 패널) 기준으로 초기화한다.
 */
(function () {
    'use strict';

    const PART_FOLDERS = {
        '1': 'brain',
        '2': 'thorax',
        '3': 'Abdomen',
        '4': 'Knee',
    };
    const MODAL_FOLDERS = {
        '1': 'X-ray',
        '2': 'CT',
        '3': 'MRI',
    };
    const ASSETS_ROOT = '/stylesheets/assets';

    function buildRemoteDir(part, modal) {
        const partFolder = PART_FOLDERS[String(part || '').trim()];
        if (!partFolder) return ASSETS_ROOT;
        let path = ASSETS_ROOT + '/' + partFolder;
        const modalFolder = MODAL_FOLDERS[String(modal || '').trim()];
        if (modalFolder) path += '/' + modalFolder;
        return path;
    }

    function u() {
        return typeof utils !== 'undefined' ? utils : null;
    }

    function bind(scope) {
        const rootEl = scope && scope.querySelector ? scope : document;
        const form = rootEl.querySelector('#studyCreateForm') || rootEl.querySelector('form[name="studyCreate"]');
        if (!form || form.dataset.bound === '1') return;
        form.dataset.bound = '1';

        function syncRemoteDir() {
            if (!form.remote_dir) return;
            form.remote_dir.value = buildRemoteDir(form.st_part.value, form.st_modal.value);
        }

        async function handleSubmit(e) {
            e.preventDefault();
            const util = u();
            const fileInput = form.file;
            const resultBox = form.parentElement
                ? form.parentElement.querySelector('#studyCreateResult')
                : document.getElementById('studyCreateResult');
            const btn = form.querySelector('#studyCreateSubmitBtn');

            if (!form.st_part.value) {
                if (util && util.showToast) util.showToast('학습 부위를 선택해주세요.');
                return;
            }
            if (!form.st_modal.value) {
                if (util && util.showToast) util.showToast('영상 종류를 선택해주세요.');
                return;
            }
            if (!form.st_disease.value.trim()) {
                if (util && util.showToast) util.showToast('병명을 입력해주세요.');
                return;
            }
            if (!fileInput.files || !fileInput.files[0]) {
                if (util && util.showToast) util.showToast('이미지 파일을 선택해주세요.');
                return;
            }

            syncRemoteDir();

            const fd = new FormData();
            fd.append('file', fileInput.files[0]);
            fd.append('st_part', form.st_part.value);
            fd.append('st_modal', form.st_modal.value);
            fd.append('st_disease', form.st_disease.value.trim());
            fd.append('remote_dir', form.remote_dir.value.trim());

            if (btn) btn.disabled = true;
            if (resultBox) {
                resultBox.classList.add('d-none');
                resultBox.textContent = '';
            }
            if (util && util.showLoading) util.showLoading(1);

            try {
                const res = await fetch('/admin/study', {
                    method: 'POST',
                    body: fd,
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    let msg = '등록 실패';
                    if (typeof data.detail === 'string') msg = data.detail;
                    else if (data.detail && data.detail.message) msg = data.detail.message;
                    throw new Error(msg);
                }
                const path = data.st_image || data.remote_path || '';
                if (resultBox) {
                    resultBox.classList.remove('d-none', 'alert-danger');
                    resultBox.classList.add('alert-success');
                    resultBox.innerHTML =
                        '등록 완료 (idx=' + data.idx + ')<br>' +
                        '<code>' + String(path).replace(/</g, '&lt;') + '</code>';
                }
                if (util && util.showToast) util.showToast('학습이 등록되었습니다.');
                form.reset();
                syncRemoteDir();
            } catch (err) {
                if (resultBox) {
                    resultBox.classList.remove('d-none', 'alert-success');
                    resultBox.classList.add('alert-danger');
                    resultBox.textContent = String(err.message || err);
                }
                if (util && util.showToast) util.showToast(String(err.message || err));
            } finally {
                if (btn) btn.disabled = false;
                if (util && util.showLoading) util.showLoading(0);
            }
        }

        form.addEventListener('submit', handleSubmit);
        if (form.st_part) form.st_part.addEventListener('change', syncRemoteDir);
        if (form.st_modal) form.st_modal.addEventListener('change', syncRemoteDir);
        syncRemoteDir();
    }

    window.initStudyCreate = bind;

    document.addEventListener('DOMContentLoaded', function () {
        bind(document);
    });
    if (document.readyState !== 'loading') {
        bind(document);
    }
})();
