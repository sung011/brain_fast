/**
 * 관리자 Dashboard (/admin)
 */
(function () {
    'use strict';

    let trendChart = null;
    let solutionChart = null;

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
            return d.toLocaleString('ko-KR', {
                month: 'numeric',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
            });
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

    function fillCards(cards) {
        const root = document.getElementById('dashCards');
        if (!root || !cards) return;
        const set = function (key, value) {
            const el = root.querySelector('[data-card="' + key + '"]');
            if (el) el.textContent = value;
        };
        set('user_count', cards.user_count);
        set('study_count', cards.study_count);
        set('today_reviews', cards.today_reviews);
        set('correct_rate', cards.correct_rate);
        const hint = root.querySelector('[data-card="correct_hint"]');
        if (hint) {
            hint.textContent = cards.total_reviews
                ? ('정답 ' + cards.correct_count + ' / 전체 ' + cards.total_reviews + '건')
                : '제출 이력 없음';
        }
    }

    function renderTrend(trend) {
        const canvas = document.getElementById('dashTrendChart');
        if (!canvas || typeof Chart === 'undefined') return;
        const labels = (trend && trend.labels) || [];
        const counts = (trend && trend.counts) || [];
        if (trendChart) trendChart.destroy();
        trendChart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: '풀이 수',
                    data: counts,
                    borderColor: '#4e73df',
                    backgroundColor: 'rgba(78, 115, 223, 0.12)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                }],
            },
            options: {
                maintainAspectRatio: false,
                legend: {display: false},
                scales: {
                    yAxes: [{
                        ticks: {beginAtZero: true, precision: 0},
                        gridLines: {color: 'rgba(0,0,0,0.05)'},
                    }],
                    xAxes: [{gridLines: {display: false}}],
                },
            },
        });
    }

    function renderSolution(items) {
        const canvas = document.getElementById('dashSolutionChart');
        const legend = document.getElementById('dashSolutionLegend');
        if (!canvas || typeof Chart === 'undefined') return;
        const rows = Array.isArray(items) ? items : [];
        const colors = {
            C: '#1cc88a',
            H: '#f6c23e',
            W: '#e74a3b',
            '-': '#858796',
        };
        if (solutionChart) solutionChart.destroy();
        solutionChart = new Chart(canvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: rows.map(function (r) { return r.label; }),
                datasets: [{
                    data: rows.map(function (r) { return r.count; }),
                    backgroundColor: rows.map(function (r) {
                        return colors[r.code] || '#858796';
                    }),
                }],
            },
            options: {
                maintainAspectRatio: false,
                legend: {display: false},
                cutoutPercentage: 65,
            },
        });
        if (legend) {
            const total = rows.reduce(function (s, r) { return s + (r.count || 0); }, 0);
            legend.innerHTML = rows.map(function (r) {
                const pct = total ? Math.round(r.count * 1000 / total) / 10 : 0;
                return (
                    '<div class="list-group-item d-flex align-items-center justify-content-between px-0 py-2">' +
                    '<span>' + escapeHtml(r.label) + '</span>' +
                    '<span class="fw-500">' + r.count + '건 (' + pct + '%)</span>' +
                    '</div>'
                );
            }).join('') || '<div class="list-group-item text-muted px-0">데이터 없음</div>';
        }
    }

    function renderRecent(items) {
        const tbody = document.getElementById('dashRecentBody');
        if (!tbody) return;
        const rows = Array.isArray(items) ? items : [];
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-muted px-3">풀이 이력이 없습니다.</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map(function (row) {
            const member = escapeHtml(row.user_name || row.user_id || '-');
            return (
                '<tr>' +
                '<td class="px-3">' + escapeHtml(formatDate(row.created_at)) + '</td>' +
                '<td>' + member + '</td>' +
                '<td>' + (row.study_idx != null ? '#' + row.study_idx : '-') + '</td>' +
                '<td>' + solutionBadge(row.rn_solution, row.rn_solution_label) + '</td>' +
                '</tr>'
            );
        }).join('');
    }

    function renderPartModal(items) {
        const list = document.getElementById('dashPartModalList');
        if (!list) return;
        const rows = Array.isArray(items) ? items : [];
        if (!rows.length) {
            list.innerHTML = '<div class="list-group-item text-muted">등록된 학습이 없습니다.</div>';
            return;
        }
        list.innerHTML = rows.map(function (row) {
            return (
                '<div class="list-group-item d-flex justify-content-between align-items-center">' +
                '<span>' + escapeHtml(row.part_label) + ' · ' + escapeHtml(row.modal_label) + '</span>' +
                '<span class="badge bg-primary rounded-pill">' + row.count + '</span>' +
                '</div>'
            );
        }).join('');
    }

    function renderHealth(health) {
        const list = document.getElementById('dashHealthList');
        if (!list) return;
        const db = (health && health.db) || {};
        const nas = (health && health.nas) || {};
        const row = function (label, ok, detail, okLabel) {
            const badge = ok
                ? '<span class="badge bg-success">' + escapeHtml(okLabel || '정상') + '</span>'
                : '<span class="badge bg-danger">확인 필요</span>';
            return (
                '<div class="list-group-item d-flex justify-content-between align-items-center">' +
                '<div><div>' + escapeHtml(label) + '</div>' +
                (detail ? '<div class="text-muted">' + escapeHtml(detail) + '</div>' : '') +
                '</div>' + badge + '</div>'
            );
        };
        list.innerHTML =
            row('PostgreSQL', !!db.ok, db.name || db.error || '', '정상') +
            row(
                'NAS',
                !!nas.enabled || !!nas.ok,
                nas.enabled || nas.ok ? '설정됨 (연결 검사는 생략)' : (nas.error || '미설정'),
                '설정됨'
            );
    }

    function applyStats(data) {
        if (!data) return;
        fillCards(data.cards);
        renderTrend(data.trend);
        renderSolution(data.solution);
        renderRecent(data.recent_reviews);
        renderPartModal(data.study_by_part_modal);
        renderHealth(data.health);
    }

    async function loadDashboard() {
        try {
            const res = await fetch('/admin/dashboard/stats', {
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            const data = await res.json();
            if (!res.ok) throw new Error((data && data.detail) || '통계 조회 실패');
            applyStats(data);
        } catch (err) {
            const tbody = document.getElementById('dashRecentBody');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="4" class="text-danger px-3">대시보드를 불러오지 못했습니다.</td></tr>';
            }
            if (typeof utils !== 'undefined' && utils.showToast) {
                utils.showToast(String(err.message || err));
            }
        }
    }

    function bindSSE() {
        if (window.__dashboardSSEBound || !window.AdminReviewSSE) return;
        window.__dashboardSSEBound = true;
        window.AdminReviewSSE.on('review_created', loadDashboard);
        if (window.AdminUserSSE) {
            window.AdminUserSSE.on('user_created', loadDashboard);
            window.AdminUserSSE.on('user_deleted', loadDashboard);
        }
        if (window.AdminStudySSE) {
            window.AdminStudySSE.on('study_created', loadDashboard);
            window.AdminStudySSE.on('study_updated', loadDashboard);
            window.AdminStudySSE.on('study_deleted', loadDashboard);
        }
    }

    function init() {
        if (!document.getElementById('dashboardRoot')) return;
        if (window.__DASHBOARD_BOOTSTRAP__) {
            applyStats(window.__DASHBOARD_BOOTSTRAP__);
        } else {
            loadDashboard();
        }
        bindSSE();
    }

    document.addEventListener('DOMContentLoaded', init);
    if (document.readyState !== 'loading') init();
})();
