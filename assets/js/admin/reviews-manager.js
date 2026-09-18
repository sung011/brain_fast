/**
 * 풀이 이력 (/admin/reviews) — DataTables 검색·페이징
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

    function solutionBadge(code, label) {
        const text = label || code || '-';
        let cls = 'bg-secondary';
        if (code === 'C') cls = 'bg-success';
        else if (code === 'H') cls = 'bg-warning text-dark';
        else if (code === 'W') cls = 'bg-danger';
        return '<span class="badge ' + cls + '">' + escapeHtml(text) + '</span>';
    }

    function imageCell(row) {
        const url = row.rn_image_url || row.rn_image;
        if (!url) return '<span class="text-muted">-</span>';
        const safe = escapeHtml(url);
        return '<a href="' + safe + '" target="_blank" rel="noopener">' +
            '<img src="' + safe + '" alt="제출" style="max-height:48px;max-width:72px;object-fit:cover;" ' +
            'class="rounded border" loading="lazy"></a>';
    }

    function memberLabel(row) {
        const name = row.user_name || '-';
        if (!row.user_id) return escapeHtml(name);
        return escapeHtml(name) +
            ' <span class="text-muted small">(' + escapeHtml(row.user_id) + ')</span>';
    }

    function reloadReviewTables() {
        if (typeof window.jQuery === 'undefined') return;
        const $ = window.jQuery;
        $('.datatable-reviews').each(function () {
            if ($.fn.DataTable.isDataTable(this)) {
                $(this).DataTable().ajax.reload(null, false);
            }
        });
    }

    function bindSSE() {
        if (window.__reviewsManagerSSEBound || !window.AdminReviewSSE) {
            return;
        }
        window.__reviewsManagerSSEBound = true;
        window.AdminReviewSSE.on('review_created', reloadReviewTables);
    }

    function initDataTables(scope) {
        if (typeof window.jQuery === 'undefined' || !window.jQuery.fn.DataTable) {
            return;
        }
        const $ = window.jQuery;
        const root = scope || document;

        $(root).find('.datatable-reviews').each(function () {
            const $table = $(this);
            if ($.fn.DataTable.isDataTable($table)) {
                return;
            }

            $table.DataTable({
                destroy: true,
                ajax: {
                    url: '/admin/reviews_all',
                    type: 'GET',
                    dataSrc: '',
                },
                columns: [
                    {
                        data: 'created_at',
                        render: function (data) {
                            return escapeHtml(formatDate(data));
                        },
                    },
                    {
                        data: 'user_name',
                        render: function (data, type, row) {
                            if (type === 'filter' || type === 'sort') {
                                return [row.user_name, row.user_id].filter(Boolean).join(' ');
                            }
                            return memberLabel(row);
                        },
                    },
                    {
                        data: 'study_idx',
                        render: function (data) {
                            return data != null ? '#' + data : '-';
                        },
                    },
                    {
                        data: 'st_part_label',
                        defaultContent: '-',
                        render: function (data) {
                            return escapeHtml(data || '-');
                        },
                    },
                    {
                        data: 'st_modal_label',
                        defaultContent: '-',
                        render: function (data) {
                            return escapeHtml(data || '-');
                        },
                    },
                    {
                        data: 'st_disease',
                        render: function (data, type, row) {
                            return escapeHtml(data || row.rn_disease || '-');
                        },
                    },
                    {
                        data: 'rn_solution',
                        render: function (data, type, row) {
                            if (type === 'filter' || type === 'sort') {
                                return row.rn_solution_label || data || '';
                            }
                            return solutionBadge(data, row.rn_solution_label);
                        },
                    },
                    {
                        data: 'rn_image',
                        orderable: false,
                        searchable: false,
                        render: function (data, type, row) {
                            return imageCell(row);
                        },
                    },
                ],
                language: {
                    emptyTable: '풀이 이력이 없습니다.',
                    zeroRecords: '검색 결과가 없습니다.',
                    search: '검색:',
                    lengthMenu: '_MENU_개씩 보기',
                    info: '_START_ - _END_ / 총 _TOTAL_건',
                    infoEmpty: '0 건',
                    infoFiltered: '(전체 _MAX_건 중 필터)',
                    paginate: {previous: '이전', next: '다음'},
                },
                order: [[0, 'desc']],
                pageLength: 10,
                lengthMenu: [[10, 25, 50, 100], [10, 25, 50, 100]],
            });
        });
    }

    function initReviewsManager(scope) {
        initDataTables(scope);
        bindSSE();
        if (window.feather) feather.replace();
    }

    window.initReviewsManager = initReviewsManager;

    document.addEventListener('DOMContentLoaded', function () {
        initReviewsManager(document);
    });
    if (document.readyState !== 'loading') {
        initReviewsManager(document);
    }
})();
