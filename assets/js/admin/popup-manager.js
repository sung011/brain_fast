/**
 * 홍보 팝업 목록 (/admin/popup)
 */
(function () {
    'use strict';

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
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

    function formatPeriod(startAt, endAt) {
        if (!startAt && !endAt) return '상시';
        return escapeHtml(formatDate(startAt)) + ' ~ ' + escapeHtml(formatDate(endAt));
    }

    function stateBadge(state) {
        if (state === 'N') return '<span class="badge bg-success">노출</span>';
        return '<span class="badge bg-secondary">숨김</span>';
    }

    async function loadPopups(table) {
        const tbody = table && table.querySelector('tbody');
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="8" class="text-muted">불러오는 중…</td></tr>';
        try {
            const res = await fetch('/admin/popups_all', {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            const rows = await res.json();
            if (!res.ok) throw new Error((rows && rows.detail) || '목록 조회 실패');
            if (!Array.isArray(rows) || rows.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="text-muted">등록된 팝업이 없습니다.</td></tr>';
                return;
            }
            tbody.innerHTML = rows.map(function (row) {
                const url = row.pp_image_url || row.pp_image;
                const img = url
                    ? ('<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener">' +
                        '<img src="' + escapeHtml(url) + '" alt="" ' +
                        'style="max-height:48px;max-width:80px;object-fit:cover;" ' +
                        'class="rounded border" loading="lazy"></a>')
                    : '<span class="text-muted">-</span>';
                return (
                    '<tr data-idx="' + row.idx + '">' +
                    '<td>' + row.idx + '</td>' +
                    '<td>' + escapeHtml(row.pp_title || '-') + '</td>' +
                    '<td>' + img + '</td>' +
                    '<td>' + (row.pp_sort != null ? row.pp_sort : 0) + '</td>' +
                    '<td class="small">' + formatPeriod(row.start_at, row.end_at) + '</td>' +
                    '<td>' + stateBadge(row.state) + '</td>' +
                    '<td>' + escapeHtml(formatDate(row.created_at)) + '</td>' +
                    '<td class="text-end text-nowrap">' +
                    '<a class="btn btn-sm btn-outline-primary me-1 open-in-tab" ' +
                    'href="/admin/popup/' + row.idx + '?partial=1" data-tab-title="팝업 #' + row.idx + '">수정</a>' +
                    '<button type="button" class="btn btn-sm btn-outline-danger popup-delete-btn" data-idx="' +
                    row.idx + '">삭제</button>' +
                    '</td>' +
                    '</tr>'
                );
            }).join('');
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-danger">목록을 불러오지 못했습니다.</td></tr>';
            if (typeof utils !== 'undefined' && utils.showToast) {
                utils.showToast(String(err.message || err));
            }
        }
    }

    async function deletePopup(idx, table) {
        if (typeof utils !== 'undefined' && utils.showConfirm) {
            const confirmResult = await utils.showConfirm('팝업 #' + idx + ' 을(를) 삭제할까요?');
            if (!confirmResult.isConfirmed) return;
        } else if (!window.confirm('팝업 #' + idx + ' 을(를) 삭제할까요?')) {
            return;
        }
        try {
            const res = await fetch('/admin/popup/' + idx, {
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
            loadPopups(table);
        } catch (err) {
            if (typeof utils !== 'undefined' && utils.showError) {
                utils.showError(String(err.message || err));
            } else {
                alert(String(err.message || err));
            }
        }
    }

    function bindActions(scope) {
        const root = scope || document;
        root.querySelectorAll('#popupTable').forEach(function (table) {
            table.addEventListener('click', function (event) {
                const btn = event.target.closest('.popup-delete-btn');
                if (!btn) return;
                event.preventDefault();
                deletePopup(btn.getAttribute('data-idx'), table);
            });
        });
    }

    function initPopupManager(scope) {
        const root = scope || document;
        root.querySelectorAll('#popupTable').forEach(function (table) {
            loadPopups(table);
        });
        bindActions(root);
        if (window.feather) feather.replace();
    }

    window.initPopupManager = initPopupManager;

    document.addEventListener('DOMContentLoaded', function () {
        if (document.getElementById('popupTable')) initPopupManager(document);
    });
})();
