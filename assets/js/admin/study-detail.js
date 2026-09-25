/**
 * 학습 상세/수정 (/admin/study/{idx})
 * scope(탭 패널) 기준으로 초기화해 여러 상세 탭이 열려도 동작한다.
 */
(function () {
    'use strict';

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
        let slides = [];
        let slideIndex = 0;
        let fadeToken = 0;

        function imageUrl(stImage) {
            if (!stImage) return '';
            if (/^https?:\/\//i.test(stImage)) return stImage;
            const path = stImage.startsWith('/') ? stImage : ('/' + stImage);
            return publicBase ? (publicBase + path) : path;
        }

        function slidePaths(stImage) {
            const text = String(stImage || '').trim();
            if (text.charAt(0) !== '[') return null;
            try {
                const arr = JSON.parse(text);
                if (!Array.isArray(arr)) return null;
                return arr.filter(function (item) {
                    return typeof item === 'string' && item;
                });
            } catch (e) {
                return null;
            }
        }

        function escapeHtml(value) {
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        }

        function showImage(path, animate) {
            const img = root.querySelector('#studyDetailImage');
            const empty = root.querySelector('#studyDetailImageEmpty');
            const pathEl = root.querySelector('#studyDetailImagePath');
            const url = imageUrl(path);
            if (pathEl) {
                pathEl.innerHTML = path ? ('<code>' + escapeHtml(path) + '</code>') : '';
            }
            if (!url) {
                if (img) {
                    img.classList.add('d-none');
                    img.removeAttribute('src');
                    img.style.opacity = '1';
                }
                if (empty) {
                    empty.textContent = '이미지가 없습니다.';
                    empty.classList.remove('d-none');
                }
                return;
            }
            if (empty) empty.classList.add('d-none');
            if (!img) return;
            img.classList.remove('d-none');
            const token = ++fadeToken;
            const apply = function () {
                if (token !== fadeToken) return;
                img.src = url;
                img.style.opacity = '1';
            };
            img.onerror = function () {
                if (token !== fadeToken) return;
                img.classList.add('d-none');
                img.style.opacity = '1';
                if (empty) {
                    empty.textContent = '이미지를 불러오지 못했습니다.';
                    empty.classList.remove('d-none');
                }
            };
            if (!animate || !img.getAttribute('src')) {
                img.style.opacity = '1';
                img.src = url;
                return;
            }
            img.style.opacity = '0.45';
            const loader = new Image();
            loader.onload = function () {
                window.setTimeout(apply, 90);
            };
            loader.onerror = function () {
                apply();
            };
            loader.src = url;
        }

        function preloadNearby(index) {
            [index - 1, index, index + 1].forEach(function (i) {
                if (i < 0 || i >= slides.length) return;
                const preload = new Image();
                preload.src = imageUrl(slides[i]);
            });
        }

        function updateSlideNav() {
            const nav = root.querySelector('[data-slide-nav]');
            const countEl = root.querySelector('[data-slide-count]');
            const prev = root.querySelector('[data-slide-prev]');
            const next = root.querySelector('[data-slide-next]');
            const visible = slides.length > 1;
            if (nav) {
                nav.classList.toggle('d-none', !visible);
                nav.classList.toggle('d-flex', visible);
            }
            if (countEl) {
                countEl.textContent = slides.length
                    ? ((slideIndex + 1) + ' / ' + slides.length)
                    : '';
            }
            if (prev) prev.disabled = slideIndex <= 0;
            if (next) next.disabled = slideIndex >= slides.length - 1;
            const stageEl = root.querySelector('[data-slide-stage]');
            if (stageEl) stageEl.style.cursor = slides.length > 1 ? 'ns-resize' : '';
        }

        function showSlide(index) {
            if (!slides.length) {
                slideIndex = 0;
                updateSlideNav();
                showImage('', false);
                return;
            }
            const next = Math.max(0, Math.min(index, slides.length - 1));
            const animate = next !== slideIndex;
            slideIndex = next;
            updateSlideNav();
            preloadNearby(slideIndex);
            showImage(slides[slideIndex], animate);
        }

        function setPreview(stImage) {
            const parsed = slidePaths(stImage);
            if (parsed && parsed.length) {
                slides = parsed;
                showSlide(0);
                return;
            }
            slides = [];
            slideIndex = 0;
            updateSlideNav();
            showImage(parsed ? '' : stImage, false);
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
            setPreview(row.st_image);
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

            const fd = new FormData();
            fd.append('st_part', form.st_part.value);
            fd.append('st_modal', form.st_modal.value);
            fd.append('st_disease', form.st_disease.value.trim());

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
        const delBtn = root.querySelector('#studyDetailDeleteBtn');
        if (delBtn) delBtn.addEventListener('click', handleDelete);
        const prevBtn = root.querySelector('[data-slide-prev]');
        const nextBtn = root.querySelector('[data-slide-next]');
        const stage = root.querySelector('[data-slide-stage]');
        if (prevBtn) {
            prevBtn.addEventListener('click', function () {
                showSlide(slideIndex - 1);
            });
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', function () {
                showSlide(slideIndex + 1);
            });
        }
        if (stage) {
            stage.addEventListener('keydown', function (ev) {
                if (!slides.length) return;
                if (ev.key === 'ArrowLeft') {
                    ev.preventDefault();
                    showSlide(slideIndex - 1);
                } else if (ev.key === 'ArrowRight') {
                    ev.preventDefault();
                    showSlide(slideIndex + 1);
                }
            });
            let wheelLock = 0;
            stage.addEventListener('wheel', function (ev) {
                if (slides.length < 2) return;
                ev.preventDefault();
                const now = Date.now();
                if (now - wheelLock < 110) return;
                if (Math.abs(ev.deltaY) < 4) return;
                wheelLock = now;
                showSlide(slideIndex + (ev.deltaY > 0 ? 1 : -1));
            }, {passive: false});
        }
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
