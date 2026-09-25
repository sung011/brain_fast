/**
 * 학습 목록 (/admin/study)
 * scope(탭 패널) 기준으로 초기화해 여러 탭이 열려도 동작한다.
 */
(function () {
    'use strict';

    function partLabels() {
        return window.STUDY_PART_LABELS || {1: '뇌', 2: '흉부', 3: '복부', 4: '무릎'};
    }

    function modalLabels() {
        return window.STUDY_MODAL_LABELS || {1: 'X-ray', 2: 'CT', 3: 'MRI'};
    }

    function labelOf(map, code) {
        if (code == null || code === '') return '-';
        const key = String(code).trim();
        return map[key] ? (key + ': ' + map[key]) : key;
    }

    function formatDate(value) {
        if (!value) return '-';
        try {
            const d = new Date(value);
            if (Number.isNaN(d.getTime())) return String(value);
            return d.toLocaleString('ko-KR');
        } catch (e) {
            return String(value);
        }
    }

    async function loadStudies(table) {
        const tbody = table && table.querySelector('tbody');
        if (!tbody) return;
        const PART = partLabels();
        const MODAL = modalLabels();
        tbody.innerHTML = '<tr><td colspan="7" class="text-muted">불러오는 중…</td></tr>';
        try {
            const res = await fetch('/admin/studies_all', {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            const rows = await res.json();
            if (!res.ok) throw new Error((rows && rows.detail) || '목록 조회 실패');
            if (!Array.isArray(rows) || rows.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="text-muted">등록된 학습이 없습니다.</td></tr>';
                return;
            }
            tbody.innerHTML = rows.map(function (row) {
                const path = row.st_image || '';
                let shortPath = path.length > 48 ? ('…' + path.slice(-48)) : path;
                const trimmed = path.trim();
                if (trimmed.charAt(0) === '[') {
                    try {
                        const slides = JSON.parse(trimmed);
                        if (Array.isArray(slides)) shortPath = '슬라이드 ' + slides.length + '장';
                    } catch (e) { /* 경로 문자열 그대로 */ }
                }
                return (
                    '<tr data-idx="' + row.idx + '">' +
                    '<td>' + row.idx + '</td>' +
                    '<td>' + labelOf(PART, row.st_part) + '</td>' +
                    '<td>' + labelOf(MODAL, row.st_modal) + '</td>' +
                    '<td>' + (row.st_disease || '-') + '</td>' +
                    '<td title="' + path.replace(/"/g, '&quot;') + '"><code class="small">' +
                    shortPath.replace(/</g, '&lt;') + '</code></td>' +
                    '<td>' + formatDate(row.created_at) + '</td>' +
                    '<td class="text-end text-nowrap">' +
                    '<a class="btn btn-sm btn-outline-primary me-1 open-in-tab" ' +
                    'href="/admin/study/' + row.idx + '?partial=1" data-tab-title="학습 #' + row.idx + '">상세</a>' +
                    '<button type="button" class="btn btn-sm btn-outline-danger study-delete-btn" data-idx="' +
                    row.idx + '">삭제</button>' +
                    '</td>' +
                    '</tr>'
                );
            }).join('');
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-danger">목록을 불러오지 못했습니다.</td></tr>';
            if (typeof utils !== 'undefined' && utils.showToast) {
                utils.showToast(String(err.message || err));
            }
        }
    }

    function refreshAllStudyTables() {
        document.querySelectorAll('#studyTable').forEach(function (table) {
            loadStudies(table);
        });
    }

    async function deleteStudy(idx, table) {
        if (typeof utils === 'undefined' || !utils.showConfirm) {
            if (!window.confirm('학습 #' + idx + ' 을(를) 삭제할까요?')) return;
        } else {
            const confirmResult = await utils.showConfirm('학습 #' + idx + ' 을(를) 삭제할까요?');
            if (!confirmResult.isConfirmed) return;
        }
        try {
            const res = await fetch('/admin/study/' + idx, {
                method: 'DELETE',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            const data = await res.json().catch(function () { return {}; });
            if (!res.ok) {
                const msg = (data.detail && data.detail.message) || data.detail || '삭제 실패';
                throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
            }
            if (typeof utils !== 'undefined' && utils.showAlert) {
                utils.showAlert('삭제되었습니다.');
            }
            if (table) await loadStudies(table);
            else refreshAllStudyTables();
        } catch (err) {
            if (typeof utils !== 'undefined' && utils.showError) {
                utils.showError(String(err.message || err));
            } else if (typeof utils !== 'undefined' && utils.showToast) {
                utils.showToast(String(err.message || err));
            } else {
                window.alert(String(err.message || err));
            }
        }
    }

    function bindSse() {
        if (window.__studyManagerSSEBound) return;
        window.__studyManagerSSEBound = true;
        const refresh = refreshAllStudyTables;
        if (window.AdminStudySSE) {
            AdminStudySSE.on('study_created', refresh);
            AdminStudySSE.on('study_updated', refresh);
            AdminStudySSE.on('study_deleted', refresh);
        } else {
            document.addEventListener('admin:study:study_created', refresh);
            document.addEventListener('admin:study:study_updated', refresh);
            document.addEventListener('admin:study:study_deleted', refresh);
        }
    }

    function bind(scope) {
        const root = scope && scope.querySelector ? scope : document;
        const table = root.querySelector('#studyTable');
        if (!table || table.dataset.bound === '1') return;
        table.dataset.bound = '1';
        table.addEventListener('click', function (e) {
            const btn = e.target.closest('.study-delete-btn');
            if (!btn) return;
            deleteStudy(btn.getAttribute('data-idx'), table);
        });
        bindSse();
        loadStudies(table);
    }

    window.initStudyManager = bind;

    document.addEventListener('DOMContentLoaded', function () {
        bind(document);
    });
    if (document.readyState !== 'loading') {
        bind(document);
    }
})();
