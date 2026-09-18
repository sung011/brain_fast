/**
 * Q&A 게시판 목록 (/admin/qa)
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
        const s = String(value).replace('T', ' ');
        return s.length > 19 ? s.slice(0, 19) : s;
    }

    function setUnreadBadge(root, total) {
        const badge = root.querySelector('#qaUnreadBadge');
        const n = Number(total) || 0;
        if (badge) {
            badge.textContent = String(n);
            badge.classList.toggle('d-none', n <= 0);
        }
        document.dispatchEvent(new CustomEvent('admin:qa:unread', {
            detail: {unread_total: n},
        }));
        if (window.AdminMessageCenter && window.AdminMessageCenter.setBadge) {
            window.AdminMessageCenter.setBadge(n);
        }
    }

    function initQaManager(scope) {
        if (typeof window.jQuery === 'undefined' || !window.jQuery.fn.DataTable) {
            return;
        }
        const $ = window.jQuery;
        const root = (scope && scope.querySelector)
            ? (scope.querySelector('#qaPageRoot') || document.getElementById('qaPageRoot'))
            : document.getElementById('qaPageRoot');
        if (!root || root.dataset.bound === '1') return;
        root.dataset.bound = '1';

        const $table = $(root).find('#qaTable');
        if (!$table.length || $.fn.DataTable.isDataTable($table)) return;

        $table.DataTable({
            destroy: true,
            order: [[5, 'desc']],
            ajax: {
                url: '/admin/qa/threads?limit=200',
                type: 'GET',
                dataSrc: function (json) {
                    setUnreadBadge(root, json.unread_total);
                    return json.items || [];
                },
            },
            columns: [
                {
                    data: 'idx',
                    width: '60px',
                    render: function (data) {
                        return escapeHtml(data);
                    },
                },
                {
                    data: 'state',
                    width: '80px',
                    render: function (data) {
                        if (data === 'S') {
                            return '<span class="badge bg-secondary">종료</span>';
                        }
                        return '<span class="badge bg-success">진행중</span>';
                    },
                },
                {
                    data: 'qt_title',
                    render: function (data, type, row) {
                        const title = data || '(제목 없음)';
                        const unread = Number(row.qt_unread_admin) > 0;
                        const cls = unread ? ' fw-bold' : '';
                        return '<a class="open-in-tab text-decoration-none' + cls + '" href="/admin/qa/' +
                            row.idx + '?partial=1" data-tab-title="문의 #' + row.idx + '">' +
                            escapeHtml(title) + '</a>';
                    },
                },
                {
                    data: 'user_name',
                    render: function (data, type, row) {
                        const name = data || row.user_id || ('#' + (row.qt_u_idx || ''));
                        if (row.user_id) {
                            return escapeHtml(name) +
                                ' <span class="text-muted small">(' + escapeHtml(row.user_id) + ')</span>';
                        }
                        return escapeHtml(name);
                    },
                },
                {
                    data: 'qt_last_msg',
                    render: function (data) {
                        const text = data || '-';
                        const clipped = text.length > 40 ? text.slice(0, 40) + '…' : text;
                        return '<span class="text-muted">' + escapeHtml(clipped) + '</span>';
                    },
                },
                {
                    data: 'qt_last_at',
                    render: function (data, type, row) {
                        return escapeHtml(formatDate(data || row.created_at));
                    },
                },
                {
                    data: 'qt_unread_admin',
                    width: '90px',
                    className: 'text-center',
                    render: function (data) {
                        const n = Number(data) || 0;
                        if (n <= 0) {
                            return '<span class="text-muted">-</span>';
                        }
                        return '<span class="badge bg-danger">미읽음 ' + n + '</span>';
                    },
                },
                {
                    data: 'idx',
                    orderable: false,
                    width: '90px',
                    className: 'text-end',
                    render: function (data) {
                        return '<a class="btn btn-sm btn-primary open-in-tab" href="/admin/qa/' +
                            data + '?partial=1" data-tab-title="문의 #' + data + '">채팅</a>';
                    },
                },
            ],
            createdRow: function (row, data) {
                if (Number(data.qt_unread_admin) > 0) {
                    $(row).addClass('table-warning');
                }
            },
            language: {
                emptyTable: '문의 내역이 없습니다.',
                zeroRecords: '검색 결과가 없습니다.',
            },
        });

        if (window.AdminQaSSE && !window.__qaBoardSSEBound) {
            window.__qaBoardSSEBound = true;
            const reload = function () {
                if ($.fn.DataTable.isDataTable($table)) {
                    $table.DataTable().ajax.reload(null, false);
                }
            };
            window.AdminQaSSE.on('qa_created', reload);
            window.AdminQaSSE.on('qa_message', reload);
            window.AdminQaSSE.on('qa_closed', reload);
        }
    }

    window.initQaManager = initQaManager;
})();
