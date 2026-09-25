/**
 * 학습 등록 폼 — 다중 이미지 드래그 앤 드롭 + NAS 업로드 + study 저장
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
    const MAX_FILES = 200;
    const MAX_BYTES = 20 * 1024 * 1024;
    const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|tif{1,2}|dcm|dicom)$/i;
    const SLIDE_NAME_RE = /^(\d+)_.+\.(png|jpe?g|gif|webp|bmp|tiff?)$/i;

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

    function isLikelyImage(file) {
        if (!file) return false;
        if (file.type && file.type.indexOf('image/') === 0) return true;
        return IMAGE_EXT.test(file.name || '');
    }

    function formatBytes(n) {
        const num = Number(n) || 0;
        if (num < 1024) return num + ' B';
        if (num < 1024 * 1024) return (num / 1024).toFixed(1) + ' KB';
        return (num / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function bind(scope) {
        const rootEl = scope && scope.querySelector ? scope : document;
        const form = rootEl.querySelector('#studyCreateForm') || rootEl.querySelector('form[name="studyCreate"]');
        if (!form || form.dataset.bound === '1') return;
        form.dataset.bound = '1';

        const fileInput = form.querySelector('#inputStudyFile') || form.querySelector('input[type="file"]');
        const dropzone = form.querySelector('#studyDropzone');
        const fileListEl = form.querySelector('#studyFileList');
        const fileCountEl = form.querySelector('#studyFileCount');
        const btn = form.querySelector('#studyCreateSubmitBtn');
        const modeWrap = form.querySelector('#studyUploadModeWrap');
        const slideHint = form.querySelector('#studySlideNameHint');
        const slideError = form.querySelector('#studySlideNameError');
        /** @type {File[]} */
        let selectedFiles = [];
        const previewUrls = new Map();

        function isMri() {
            return String(form.st_modal && form.st_modal.value || '') === '3';
        }

        function uploadMode() {
            const checked = form.querySelector('input[name="upload_mode"]:checked');
            return checked ? checked.value : 'each';
        }

        function isSlideMode() {
            return isMri() && uploadMode() === 'slide';
        }

        function slideOrder(file) {
            const matched = SLIDE_NAME_RE.exec((file && file.name) || '');
            return matched ? parseInt(matched[1], 10) : null;
        }

        function slideNameErrors(files) {
            const errors = [];
            const seen = {};
            files.forEach(function (file) {
                const name = (file && file.name) || '(이름 없음)';
                const order = slideOrder(file);
                if (order == null) {
                    errors.push(name + ' — 파일 이름을 01_이름.png 또는 01_이름.jpg 형식으로 수정한 뒤 등록하세요.');
                    return;
                }
                if (seen[order]) {
                    const label = String(order).padStart(2, '0');
                    errors.push(name + ' — 순서 번호 ' + label + ' 가 ' + seen[order] + ' 와 중복입니다.');
                    return;
                }
                seen[order] = name;
            });
            return errors;
        }

        function showSlideErrors(errors) {
            if (!slideError) return;
            if (!errors.length) {
                slideError.classList.add('d-none');
                slideError.textContent = '';
                return;
            }
            slideError.classList.remove('d-none');
            slideError.textContent = errors.join('\n');
        }

        function syncUploadMode() {
            const mri = isMri();
            if (modeWrap) modeWrap.classList.toggle('d-none', !mri);
            if (!mri) {
                const eachRadio = form.querySelector('#uploadModeEach');
                if (eachRadio) eachRadio.checked = true;
            }
            if (slideHint) slideHint.classList.toggle('d-none', !isSlideMode());
            if (!isSlideMode()) showSlideErrors([]);
            renderFileList();
        }

        function syncRemoteDir() {
            if (!form.remote_dir) return;
            form.remote_dir.value = buildRemoteDir(form.st_part.value, form.st_modal.value);
        }

        function syncInputFiles() {
            if (!fileInput) return;
            const dt = new DataTransfer();
            selectedFiles.forEach(function (f) {
                dt.items.add(f);
            });
            fileInput.files = dt.files;
        }

        function updateSubmitLabel() {
            if (!btn) return;
            const icon = '<i class="me-1" data-feather="upload"></i>';
            const n = selectedFiles.length;
            let label = 'NAS 업로드 후 등록';
            if (n > 0 && isSlideMode()) {
                label += ' (슬라이드 ' + n + '장, 1건)';
            } else if (n > 0) {
                label += ' (' + n + '건)';
            }
            btn.innerHTML = icon + label;
            if (window.feather) feather.replace();
        }

        function revokePreviews() {
            previewUrls.forEach(function (url) {
                try {
                    URL.revokeObjectURL(url);
                } catch (e) { /* ignore */ }
            });
            previewUrls.clear();
        }

        function sortSlideFiles() {
            if (!isSlideMode()) return;
            selectedFiles.sort(function (a, b) {
                const na = slideOrder(a);
                const nb = slideOrder(b);
                if (na == null && nb == null) return String(a.name).localeCompare(String(b.name));
                if (na == null) return 1;
                if (nb == null) return -1;
                if (na !== nb) return na - nb;
                return String(a.name).localeCompare(String(b.name));
            });
        }

        function renderFileList() {
            sortSlideFiles();
            syncInputFiles();
            if (fileCountEl) {
                fileCountEl.textContent = selectedFiles.length
                    ? selectedFiles.length + '개 파일 선택됨'
                    : '선택된 파일 없음';
            }
            updateSubmitLabel();
            if (!fileListEl) return;
            if (!selectedFiles.length) {
                fileListEl.classList.add('d-none');
                fileListEl.innerHTML = '';
                revokePreviews();
                return;
            }
            fileListEl.classList.remove('d-none');
            const keep = new Set();
            fileListEl.innerHTML = selectedFiles
                .map(function (file, idx) {
                    let thumb = '';
                    if (file.type && file.type.indexOf('image/') === 0) {
                        let url = previewUrls.get(file);
                        if (!url) {
                            url = URL.createObjectURL(file);
                            previewUrls.set(file, url);
                        }
                        keep.add(file);
                        thumb = '<img class="study-file-thumb" src="' + url + '" alt="">';
                    } else {
                        thumb = '<span class="study-file-thumb d-inline-flex align-items-center justify-content-center text-muted small">DCM</span>';
                    }
                    const order = isSlideMode() ? slideOrder(file) : null;
                    const orderLabel = order == null
                        ? ''
                        : ('<span class="badge text-bg-light border me-1">' + String(order).padStart(2, '0') + '</span>');
                    return (
                        '<div class="list-group-item">' +
                        thumb +
                        '<div class="file-name" title="' + escapeHtml(file.name) + '">' +
                        orderLabel +
                        escapeHtml(file.name) +
                        ' <span class="text-muted">(' + formatBytes(file.size) + ')</span></div>' +
                        '<button type="button" class="btn btn-sm btn-outline-danger py-0 px-2" data-remove-idx="' +
                        idx +
                        '" aria-label="제거">×</button>' +
                        '</div>'
                    );
                })
                .join('');

            previewUrls.forEach(function (url, file) {
                if (!keep.has(file)) {
                    try {
                        URL.revokeObjectURL(url);
                    } catch (e) { /* ignore */ }
                    previewUrls.delete(file);
                }
            });
        }

        function addFiles(fileList) {
            const util = u();
            const incoming = Array.prototype.slice.call(fileList || []);
            if (!incoming.length) return;

            const rejected = [];
            const tooBig = [];
            const badNames = [];
            incoming.forEach(function (file) {
                if (!isLikelyImage(file)) {
                    rejected.push(file.name || '(이름 없음)');
                    return;
                }
                if (file.size > MAX_BYTES) {
                    tooBig.push(file.name || '(이름 없음)');
                    return;
                }
                if (isSlideMode() && slideOrder(file) == null) {
                    badNames.push((file.name || '(이름 없음)') + ' — 파일 이름을 01_이름.png 또는 01_이름.jpg 형식으로 수정한 뒤 등록하세요.');
                    return;
                }
                const dup = selectedFiles.some(function (f) {
                    return f.name === file.name && f.size === file.size && f.lastModified === file.lastModified;
                });
                if (dup) return;
                selectedFiles.push(file);
            });

            if (selectedFiles.length > MAX_FILES) {
                selectedFiles = selectedFiles.slice(0, MAX_FILES);
                if (util && util.showToast) {
                    util.showToast('한 번에 최대 ' + MAX_FILES + '개까지 등록할 수 있습니다.');
                }
            }
            if (rejected.length && util && util.showToast) {
                util.showToast('이미지/DICOM이 아닌 파일은 제외했습니다: ' + rejected.slice(0, 3).join(', '));
            }
            if (tooBig.length && util && util.showToast) {
                util.showToast('20MB 초과 파일은 제외했습니다: ' + tooBig.slice(0, 3).join(', '));
            }
            if (isSlideMode()) {
                const nameErrors = slideNameErrors(selectedFiles).concat(badNames);
                showSlideErrors(nameErrors);
                if (nameErrors.length && util && util.showToast) {
                    util.showToast(nameErrors[0]);
                }
            } else {
                showSlideErrors([]);
            }
            renderFileList();
        }

        function removeAt(idx) {
            if (idx < 0 || idx >= selectedFiles.length) return;
            selectedFiles.splice(idx, 1);
            if (isSlideMode()) showSlideErrors(slideNameErrors(selectedFiles));
            renderFileList();
        }

        function clearFiles() {
            selectedFiles = [];
            renderFileList();
        }

        function keepFailedFiles(failItems) {
            const failNames = {};
            (failItems || []).forEach(function (it) {
                if (it && it.filename) failNames[it.filename] = true;
            });
            selectedFiles = selectedFiles.filter(function (f) {
                return failNames[f.name];
            });
            renderFileList();
        }

        async function handleSubmit(e) {
            e.preventDefault();
            const util = u();
            const resultBox = form.parentElement
                ? form.parentElement.querySelector('#studyCreateResult')
                : document.getElementById('studyCreateResult');

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
            if (!selectedFiles.length) {
                if (util && util.showToast) util.showToast('이미지 파일을 선택해주세요.');
                return;
            }
            if (isSlideMode()) {
                const nameErrors = slideNameErrors(selectedFiles);
                showSlideErrors(nameErrors);
                if (nameErrors.length) {
                    if (util && util.showToast) util.showToast(nameErrors[0]);
                    return;
                }
            }

            syncRemoteDir();

            const fd = new FormData();
            selectedFiles.forEach(function (f) {
                fd.append('files', f, f.name);
            });
            fd.append('st_part', form.st_part.value);
            fd.append('st_modal', form.st_modal.value);
            fd.append('st_disease', form.st_disease.value.trim());
            fd.append('remote_dir', form.remote_dir.value.trim());
            fd.append('upload_mode', isSlideMode() ? 'slide' : 'each');

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
                const data = await res.json().catch(function () {
                    return {};
                });
                if (!res.ok) {
                    let msg = '등록 실패';
                    if (typeof data.detail === 'string') msg = data.detail;
                    else if (data.detail && data.detail.message) msg = data.detail.message;
                    throw new Error(msg);
                }

                const items = Array.isArray(data.items) ? data.items : [];
                const okItems = items.filter(function (it) {
                    return it && it.ok;
                });
                const failItems = items.filter(function (it) {
                    return it && !it.ok;
                });
                const lines = [];
                lines.push(
                    '<strong>' +
                        escapeHtml(data.message || okItems.length + '건 등록 완료') +
                        '</strong>'
                );
                okItems.slice(0, 20).forEach(function (it) {
                    const path = it.st_image || it.remote_path || '';
                    lines.push(
                        '#' +
                            it.idx +
                            ' ' +
                            escapeHtml(it.filename || '') +
                            ' → <code>' +
                            escapeHtml(path) +
                            '</code>'
                    );
                });
                if (okItems.length > 20) {
                    lines.push('… 외 ' + (okItems.length - 20) + '건');
                }
                failItems.forEach(function (it) {
                    lines.push(
                        '<span class="text-danger">실패: ' +
                            escapeHtml(it.filename || '') +
                            ' — ' +
                            escapeHtml(it.error || '') +
                            '</span>'
                    );
                });

                if (resultBox) {
                    resultBox.classList.remove('d-none', 'alert-danger', 'alert-success', 'alert-warning');
                    resultBox.classList.add(failItems.length ? 'alert-warning' : 'alert-success');
                    resultBox.innerHTML = lines.join('<br>');
                }
                if (util && util.showToast) {
                    util.showToast(data.message || '학습이 등록되었습니다.');
                }
                // 병명은 유지(연속 등록 편의). 성공분만 목록에서 제거, 실패분은 재시도용으로 남김.
                if (failItems.length) {
                    keepFailedFiles(failItems);
                } else {
                    clearFiles();
                }
                syncRemoteDir();
            } catch (err) {
                if (resultBox) {
                    resultBox.classList.remove('d-none', 'alert-success', 'alert-warning');
                    resultBox.classList.add('alert-danger');
                    resultBox.textContent = String(err.message || err);
                }
                if (util && util.showToast) util.showToast(String(err.message || err));
            } finally {
                if (btn) btn.disabled = false;
                updateSubmitLabel();
                if (util && util.showLoading) util.showLoading(0);
            }
        }

        form.addEventListener('submit', handleSubmit);
        if (form.st_part) form.st_part.addEventListener('change', syncRemoteDir);
        if (form.st_modal) {
            form.st_modal.addEventListener('change', function () {
                syncRemoteDir();
                syncUploadMode();
            });
        }
        form.querySelectorAll('input[name="upload_mode"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                if (isSlideMode()) {
                    const kept = [];
                    const dropped = [];
                    selectedFiles.forEach(function (file) {
                        if (slideOrder(file) == null) dropped.push(file);
                        else kept.push(file);
                    });
                    selectedFiles = kept;
                    const nameErrors = slideNameErrors(selectedFiles).concat(dropped.map(function (file) {
                        return (file.name || '(이름 없음)') + ' — 파일 이름을 01_이름.png 또는 01_이름.jpg 형식으로 수정한 뒤 등록하세요.';
                    }));
                    showSlideErrors(nameErrors);
                    if (nameErrors.length) {
                        const util = u();
                        if (util && util.showToast) util.showToast(nameErrors[0]);
                    }
                }
                syncUploadMode();
            });
        });

        if (fileInput) {
            fileInput.addEventListener('change', function () {
                addFiles(fileInput.files);
            });
        }

        if (fileListEl) {
            fileListEl.addEventListener('click', function (ev) {
                const removeBtn = ev.target.closest('[data-remove-idx]');
                if (!removeBtn) return;
                const idx = parseInt(removeBtn.getAttribute('data-remove-idx'), 10);
                if (!isNaN(idx)) removeAt(idx);
            });
        }

        if (dropzone) {
            dropzone.addEventListener('click', function () {
                if (fileInput) fileInput.click();
            });
            dropzone.addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault();
                    if (fileInput) fileInput.click();
                }
            });

            dropzone.addEventListener('dragenter', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                dropzone.classList.add('is-dragover');
            });
            dropzone.addEventListener('dragover', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                dropzone.classList.add('is-dragover');
            });
            dropzone.addEventListener('dragleave', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                // 자식 요소로 이동한 경우는 하이라이트 유지
                if (dropzone.contains(ev.relatedTarget)) return;
                dropzone.classList.remove('is-dragover');
            });
            dropzone.addEventListener('drop', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                dropzone.classList.remove('is-dragover');
                const dt = ev.dataTransfer;
                if (dt && dt.files && dt.files.length) addFiles(dt.files);
            });
        }

        // 폼 밖 드롭 시 브라우저가 파일을 열어버리는 것 방지
        form.addEventListener('dragover', function (ev) {
            ev.preventDefault();
        });
        form.addEventListener('drop', function (ev) {
            if (dropzone && (ev.target === dropzone || dropzone.contains(ev.target))) return;
            ev.preventDefault();
        });

        syncRemoteDir();
        syncUploadMode();
    }

    window.initStudyCreate = bind;

    document.addEventListener('DOMContentLoaded', function () {
        bind(document);
    });
    if (document.readyState !== 'loading') {
        bind(document);
    }
})();
