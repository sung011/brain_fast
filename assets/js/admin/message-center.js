/**
 * 상단 Message Center — 미읽음 뱃지 + 드롭다운
 */
(function () {
    'use strict';

    const QA_BOARD_URL = '/admin/qa?partial=1';
    let listEl = null;
    let badgeEl = null;
    let sidenavBadgeEl = null;
    let footerUnread = null;

    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function relativeTime(value) {
        if (!value) return '';
        const date = new Date(String(value).replace(' ', 'T'));
        if (Number.isNaN(date.getTime())) return '';
        const diffSec = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
        if (diffSec < 60) return diffSec + 's';
        if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm';
        if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h';
        return Math.floor(diffSec / 86400) + 'd';
    }

    function applyBadge(el, n) {
        if (!el) return;
        el.textContent = n > 99 ? '99+' : String(n);
        el.classList.toggle('d-none', n <= 0);
        el.setAttribute('aria-label', '미읽음 ' + n + '건');
    }

    function setBadge(count) {
        const n = Number(count) || 0;
        applyBadge(badgeEl, n);
        applyBadge(sidenavBadgeEl, n);
        if (footerUnread) {
            footerUnread.textContent = n > 0
                ? ('전체 내역 보기 (미읽음 ' + n + ')')
                : '전체 내역 보기';
        }
    }

    function closeDropdown() {
        const toggle = document.getElementById('navbarDropdownMessages');
        if (!toggle || typeof bootstrap === 'undefined') return;
        const instance = bootstrap.Dropdown.getInstance(toggle)
            || bootstrap.Dropdown.getOrCreateInstance(toggle);
        if (instance) instance.hide();
    }

    function openTab(url, title) {
        closeDropdown();
        if (window.AdminTabs && typeof window.AdminTabs.openUrl === 'function') {
            window.AdminTabs.openUrl(url, title);
            return;
        }
        const a = document.createElement('a');
        a.href = url;
        a.setAttribute('data-tab-href', url);
        a.setAttribute('data-tab-title', title);
        a.className = 'open-in-tab';
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    function openQaBoard() {
        openTab(QA_BOARD_URL, 'Q&A');
    }

    function openQaChat(qaIdx) {
        openTab('/admin/qa/' + qaIdx + '?partial=1', '문의 #' + qaIdx);
    }

    function renderItems(items) {
        if (!listEl) return;
        const rows = (items || []).slice(0, 8);
        if (!rows.length) {
            listEl.innerHTML =
                '<div class="dropdown-item text-muted small py-3 text-center">새 문의가 없습니다.</div>';
            return;
        }
        listEl.innerHTML = rows.map(function (row) {
            const name = row.user_name || row.user_id || ('회원 #' + (row.qt_u_idx || ''));
            const preview = row.qt_last_msg || row.qt_title || '(내용 없음)';
            const unread = Number(row.qt_unread_admin) || 0;
            const hasUnread = unread > 0;
            const rel = relativeTime(row.qt_last_at || row.created_at);
            return (
                '<a class="dropdown-item dropdown-notifications-item qa-mc-item' +
                (hasUnread ? ' unread' : '') +
                '" href="#!" role="button" data-qa-idx="' + row.idx + '">' +
                '<div class="qa-mc-item-body">' +
                '<div class="dropdown-notifications-item-content-text">' +
                escapeHtml(preview) +
                '</div>' +
                '<div class="dropdown-notifications-item-content-details">' +
                escapeHtml(name) + (rel ? (' · ' + rel) : '') +
                '</div></div>' +
                (hasUnread
                    ? ('<span class="badge bg-danger qa-mc-unread">미읽음 ' + unread + '</span>')
                    : '') +
                '</a>'
            );
        }).join('');
        if (typeof window.feather !== 'undefined') {
            window.feather.replace();
        }
    }

    async function refresh() {
        try {
            const res = await fetch('/admin/qa/threads?limit=8', {
                headers: {Accept: 'application/json'},
            });
            if (!res.ok) return;
            const data = await res.json();
            setBadge(data.unread_total);
            renderItems(data.items || []);
        } catch (error) {
            // ignore
        }
    }

    function bind() {
        listEl = document.getElementById('qaMessageCenterList');
        badgeEl = document.getElementById('qaMessageCenterBadge');
        sidenavBadgeEl = document.getElementById('qaSidenavBadge');
        footerUnread = document.getElementById('qaMessageCenterFooterLabel');
        if (!listEl && !sidenavBadgeEl) return;

        const host = document.querySelector('.dropdown-notifications');
        if (host && !host.dataset.qaBound) {
            host.dataset.qaBound = '1';
            host.addEventListener('click', function (event) {
                const item = event.target.closest('a[data-qa-idx]');
                const footer = event.target.closest('.dropdown-notifications-footer');
                if (item) {
                    event.preventDefault();
                    event.stopPropagation();
                    openQaChat(item.getAttribute('data-qa-idx'));
                    return;
                }
                if (footer) {
                    event.preventDefault();
                    event.stopPropagation();
                    openQaBoard();
                }
            });
        }

        if (window.AdminQaSSE) {
            window.AdminQaSSE.on('qa_created', refresh);
            window.AdminQaSSE.on('qa_message', refresh);
            window.AdminQaSSE.on('qa_closed', refresh);
        }
        document.addEventListener('admin:qa:unread', function (event) {
            if (event && event.detail && event.detail.unread_total != null) {
                setBadge(event.detail.unread_total);
            }
        });
        refresh();
    }

    window.AdminMessageCenter = {
        refresh: refresh,
        setBadge: setBadge,
        openQaBoard: openQaBoard,
        openQaChat: openQaChat,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
