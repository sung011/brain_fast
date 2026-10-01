/**
 * 학습 목록 (/admin/study)
 * DataTables로 검색·페이징한다. scope(탭 패널) 기준으로 초기화해 여러 탭이 열려도 동작한다.
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

    function shortImagePath(path) {
        const text = path || '';
        const trimmed = text.trim();
        if (trimmed.charAt(0) === '[') {
            try {
                const slides = JSON.parse(trimmed);
                if (Array.isArray(slides)) return '슬라이드 ' + slides.length + '장';
            } catch (e) { /* 경로 문자열 그대로 */ }
        }
        if (!text) return '-';
        return text.length > 48 ? ('…' + text.slice(-48)) : text;
    }

    function reloadStudyTables() {
        if (typeof window.jQuery === 'undefined') return;
        const $ = window.jQuery;
        $('.datatable-studies').each(function () {
            if ($.fn.DataTable.isDataTable(this)) {
                $(this).DataTable().ajax.reload(null, false);
            }
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
            if (table && window.jQuery && window.jQuery.fn.DataTable.isDataTable(table)) {
                window.jQuery(table).DataTable().ajax.reload(null, false);
            } else {
                reloadStudyTables();
            }
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
        const refresh = reloadStudyTables;
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

    function initDataTables(scope) {
        if (typeof window.jQuery === 'undefined' || !window.jQuery.fn.DataTable) {
            return;
        }
        const $ = window.jQuery;
        const root = scope && scope.querySelector ? scope : document;
        const PART = partLabels();
        const MODAL = modalLabels();

        $(root).find('.datatable-studies').each(function () {
            const table = this;
            const $table = $(table);
            if ($.fn.DataTable.isDataTable(table) || table.dataset.bound === '1') {
                return;
            }
            table.dataset.bound = '1';

            table.addEventListener('click', function (e) {
                const btn = e.target.closest('.study-delete-btn');
                if (!btn) return;
                deleteStudy(btn.getAttribute('data-idx'), table);
            });

            $table.DataTable({
                destroy: true,
                paging: true,
                ajax: {
                    url: '/admin/studies_all',
                    type: 'GET',
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                    dataSrc: '',
                    error: function () {
                        const msg = '목록을 불러오지 못했습니다.';
                        if (typeof utils !== 'undefined' && utils.showToast) {
                            utils.showToast(msg);
                        }
                    },
                },
                columns: [
                    {
                        data: null,
                        orderable: false,
                        searchable: false,
                        render: function () {
                            return '';
                        },
                    },
                    {
                        data: 'st_part',
                        render: function (data, type) {
                            const label = labelOf(PART, data);
                            return type === 'display' ? escapeHtml(label) : label;
                        },
                    },
                    {
                        data: 'st_modal',
                        render: function (data, type) {
                            const label = labelOf(MODAL, data);
                            return type === 'display' ? escapeHtml(label) : label;
                        },
                    },
                    {
                        data: 'st_disease',
                        render: function (data, type) {
                            const label = data || '-';
                            return type === 'display' ? escapeHtml(label) : label;
                        },
                    },
                    {
                        data: 'st_image',
                        render: function (data, type) {
                            const path = data || '';
                            if (type === 'filter' || type === 'sort') return path;
                            const shortPath = shortImagePath(path);
                            return '<code class="small" title="' + escapeHtml(path) + '">' +
                                escapeHtml(shortPath) + '</code>';
                        },
                    },
                    {
                        data: 'created_at',
                        render: function (data, type) {
                            const time = data ? Date.parse(data) : NaN;
                            if (type === 'sort' || type === 'type') {
                                return Number.isNaN(time) ? 0 : time;
                            }
                            if (type !== 'display') return data || '';
                            return escapeHtml(formatDate(data));
                        },
                    },
                    {
                        data: 'idx',
                        orderable: false,
                        searchable: false,
                        className: 'text-end text-nowrap',
                        render: function (data) {
                            return '<a class="btn btn-sm btn-outline-primary me-1 open-in-tab" ' +
                                'href="/admin/study/' + data + '?partial=1" data-tab-title="학습 #' + data + '">상세</a>' +
                                '<button type="button" class="btn btn-sm btn-outline-danger study-delete-btn" data-idx="' +
                                data + '">삭제</button>';
                        },
                    },
                ],
                language: {
                    emptyTable: '등록된 학습이 없습니다.',
                    zeroRecords: '검색 결과가 없습니다.',
                    search: '검색:',
                    lengthMenu: '_MENU_개씩 보기',
                    info: '_START_ - _END_ / 총 _TOTAL_건',
                    infoEmpty: '0 건',
                    infoFiltered: '(전체 _MAX_건 중 필터)',
                    paginate: {previous: '이전', next: '다음'},
                },
                order: [[5, 'desc']],
                pageLength: 10,
                lengthMenu: [[10, 25, 50, 100], [10, 25, 50, 100]],
                drawCallback: function () {
                    const api = this.api();
                    const start = api.page.info().start;
                    api.column(0, {page: 'current'}).nodes().each(function (cell, i) {
                        cell.textContent = String(start + i + 1);
                    });
                },
            });
        });
    }

    function bind(scope) {
        initDataTables(scope);
        bindSse();
    }

    window.initStudyManager = bind;

    document.addEventListener('DOMContentLoaded', function () {
        bind(document);
    });
    if (document.readyState !== 'loading') {
        bind(document);
    }
})();
