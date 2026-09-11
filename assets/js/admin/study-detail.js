/**
 * 학습 상세/수정 (/admin/study/{idx})
 * scope(탭 패널) 기준으로 초기화해 여러 상세 탭이 열려도 동작한다.
 */
(function () {
    'use strict';

    const PART_FOLDERS = {1: 'brain', 2: 'thorax', 3: 'Abdomen', 4: 'Knee'};
    const MODAL_FOLDERS = {1: 'X-ray', 2: 'CT', 3: 'MRI'};
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
        const rootEl = (scope && scope.querySelector) ? scope : document;
        const root = rootEl.querySelector('#studyDetailRoot');
        if (!root || root.getAttribute('data-bound') === '1') return;
        root.setAttribute('data-bound', '1');

        const form = root.querySelector('form[name="studyDetail"]') || root.querySelector('#studyDetailForm');
        if (!form) return;

        const studyIdx = root.getAttribute('data-study-idx');
        const publicBase = (root.getAttribute('data-public-base') || '').replace(/\/$/, '');

        function syncRemoteDir() {
            form.remote_dir.value = buildRemoteDir(form.st_part.value, form.st_modal.value);
        }

        function imageUrl(stImage) {
            if (!stImage) return '';
            if (/^https?:\/\//i.test(stImage)) return stImage;
            const path = stImage.startsWith('/') ? stImage : ('/' + stImage);
            return publicBase ? (publicBase + path) : path;
        }

        function setPreview(stImage) {
            const img = root.querySelector('#studyDetailImage');
            const empty = root.querySelector('#studyDetailImageEmpty');
            const pathEl = root.querySelector('#studyDetailImagePath');
            const url = imageUrl(stImage);
            if (pathEl) {
                pathEl.innerHTML = stImage
                    ? ('<code>' + String(stImage).replace(/</g, '&lt;') + '</code>')
                    : '';
            }
            if (!url) {
                if (img) {
                    img.classList.add('d-none');
                    img.removeAttribute('src');
                }
                if (empty) empty.classList.remove('d-none');
                return;
            }
            if (empty) empty.classList.add('d-none');
            if (img) {
                img.classList.remove('d-none');
                img.src = url;
                img.onerror = function () {
                    img.classList.add('d-none');
                    if (empty) {
                        empty.textContent = '이미지를 불러오지 못했습니다.';
                        empty.classList.remove('d-none');
                    }
                };
            }
        }

        function fillForm(row) {
            form.idx.value = row.idx;
            form.st_part.value = String(row.st_part || '').trim();
            form.st_modal.value = String(row.st_modal || '').trim();
            form.st_disease.value = row.st_disease || '';
            const createdAt = root.querySelector('#detailCreatedAt');
            const updatedAt = root.querySelector('#detailUpdatedAt');
            if (createdAt) {
                createdAt.value = row.created_at
                    ? new Date(row.created_at).toLocaleString('ko-KR')
                    : '-';
            }
            if (updatedAt) updatedAt.value = row.updated_at || '-';
            syncRemoteDir();
            setPreview(row.st_image);
            form.file.value = '';
        }

        async function loadDetail() {
            const loading = root.querySelector('#studyDetailLoading');
            try {
                const res = await fetch('/admin/studies/' + studyIdx, {
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    const msg = (data.detail && data.detail.message) || data.detail || '조회 실패';
                    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
                }
                fillForm(data);
                if (loading) loading.classList.add('d-none');
                form.classList.remove('d-none');
                if (window.feather) feather.replace();
            } catch (err) {
                if (loading) loading.textContent = String(err.message || err);
                const util = u();
                if (util && util.showError) util.showError(String(err.message || err));
            }
        }

        async function handleSubmit(e) {
            e.preventDefault();
            const util = u();
            if (!form.st_part.value) {
                if (util && util.showToast) util.showToast('학습 부위를 선택해주세요.', form.st_part.focus());
                return;
            }
            if (!form.st_modal.value) {
                if (util && util.showToast) util.showToast('영상 종류를 선택해주세요.', form.st_modal.focus());
                return;
            }
            if (!form.st_disease.value.trim()) {
                if (util && util.showToast) util.showToast('병명을 입력해주세요.', form.st_disease.focus());
                return;
            }

            syncRemoteDir();
            const fd = new FormData();
            fd.append('st_part', form.st_part.value);
            fd.append('st_modal', form.st_modal.value);
            fd.append('st_disease', form.st_disease.value.trim());
            if (form.file.files && form.file.files[0]) {
                fd.append('file', form.file.files[0]);
            }

            const btn = root.querySelector('#studyDetailSaveBtn');
            if (btn) btn.disabled = true;
            if (util && util.showLoading) util.showLoading(1);
            try {
                const res = await fetch('/admin/study/' + studyIdx, {
                    method: 'PUT',
                    body: fd,
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    let msg = '수정 실패';
                    if (typeof data.detail === 'string') msg = data.detail;
                    else if (data.detail && data.detail.message) msg = data.detail.message;
                    throw new Error(msg);
                }
                if (util && util.showAlert) util.showAlert('저장되었습니다.');
                await loadDetail();
            } catch (err) {
                if (util && util.showError) util.showError(String(err.message || err));
                else if (util && util.showToast) util.showToast(String(err.message || err));
                else window.alert(String(err.message || err));
            } finally {
                if (btn) btn.disabled = false;
                if (util && util.showLoading) util.showLoading(0);
            }
        }

        async function handleDelete() {
            const util = u();
            let confirmed = false;
            if (util && util.showConfirm) {
                const confirmResult = await util.showConfirm('학습 #' + studyIdx + ' 을(를) 삭제할까요?');
                confirmed = !!confirmResult.isConfirmed;
            } else {
                confirmed = window.confirm('학습 #' + studyIdx + ' 을(를) 삭제할까요?');
            }
            if (!confirmed) return;

            try {
                const res = await fetch('/admin/study/' + studyIdx, {
                    method: 'DELETE',
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    const msg = (data.detail && data.detail.message) || data.detail || '삭제 실패';
                    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
                }
                if (util && util.showAlert) util.showAlert('삭제되었습니다.');
                const listLink = root.querySelector('a.open-in-tab[href*="/admin/study"]');
                if (listLink) listLink.click();
                else window.location.href = '/admin/study';
            } catch (err) {
                if (util && util.showError) util.showError(String(err.message || err));
                else if (util && util.showToast) util.showToast(String(err.message || err));
                else window.alert(String(err.message || err));
            }
        }

        form.addEventListener('submit', handleSubmit);
        form.st_part.addEventListener('change', syncRemoteDir);
        form.st_modal.addEventListener('change', syncRemoteDir);
        const delBtn = root.querySelector('#studyDetailDeleteBtn');
        if (delBtn) delBtn.addEventListener('click', handleDelete);
        form.file.addEventListener('change', function () {
            const f = form.file.files && form.file.files[0];
            if (!f) return;
            const url = URL.createObjectURL(f);
            const img = root.querySelector('#studyDetailImage');
            const empty = root.querySelector('#studyDetailImageEmpty');
            if (empty) empty.classList.add('d-none');
            if (img) {
                img.classList.remove('d-none');
                img.src = url;
            }
        });
        loadDetail();
    }

    window.initStudyDetail = bind;

    document.addEventListener('DOMContentLoaded', function () {
        bind(document);
    });
    if (document.readyState !== 'loading') {
        // 탭 로딩 시에는 tab-manager가 scope로 호출한다.
        // 전체 페이지 진입일 때만 자동 바인딩.
        if (!document.getElementById('myTabContent')) {
            bind(document);
        } else if (!document.querySelector('#myTabContent .tab-pane')) {
            bind(document);
        }
    }
})();
