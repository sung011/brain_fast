/**
 * Q&A 문의 상세 — 채팅 UI (내용만, 아바타 없음)
 */
(function () {
    'use strict';

    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function formatStamp(value) {
        if (!value) return '';
        return String(value).replace('T', ' ').slice(0, 16);
    }

    function initQaDetail(scope) {
        const root = (scope && scope.querySelector)
            ? (scope.querySelector('#qaDetailRoot') || document.getElementById('qaDetailRoot'))
            : document.getElementById('qaDetailRoot');
        if (!root || root.dataset.bound === '1') return;
        root.dataset.bound = '1';

        const idx = root.getAttribute('data-idx');
        const titleEl = root.querySelector('#qaChatTitle');
        const metaEl = root.querySelector('#qaChatMeta');
        const messageEl = root.querySelector('#qaMessageList');
        const form = root.querySelector('#qaReplyForm');
        const bodyInput = root.querySelector('#qaReplyBody');
        const closeBtn = root.querySelector('#qaCloseBtn');
        const statusBadge = root.querySelector('#qaStatusBadge');

        let memberLabel = '문의자';

        function notifyUnread(total) {
            document.dispatchEvent(new CustomEvent('admin:qa:unread', {
                detail: {unread_total: total},
            }));
            if (window.AdminMessageCenter) {
                if (window.AdminMessageCenter.setBadge) {
                    window.AdminMessageCenter.setBadge(total);
                }
                if (window.AdminMessageCenter.refresh) {
                    window.AdminMessageCenter.refresh();
                }
            }
        }

        function renderMessages(messages) {
            if (!messageEl) return;
            if (!messages || !messages.length) {
                messageEl.innerHTML =
                    '<div class="qa-messenger-empty">아직 대화가 없습니다.</div>';
                return;
            }

            messageEl.innerHTML = messages.map(function (msg) {
                const isMine = msg.qm_role === 'A';
                const who = isMine ? '나' : memberLabel;
                const side = isMine ? 'mine' : 'other';
                return (
                    '<div class="qa-chat-line qa-chat-line--' + side + '">' +
                    '<div class="qa-chat-stack">' +
                    '<span class="qa-chat-name">' + escapeHtml(who) + '</span>' +
                    '<div class="qa-chat-bubble-wrap">' +
                    '<div class="qa-chat-bubble qa-chat-bubble--' + side + '">' +
                    escapeHtml(msg.qm_body) +
                    '</div>' +
                    '<span class="qa-chat-time">' +
                    escapeHtml(formatStamp(msg.created_at)) +
                    '</span></div></div></div>'
                );
            }).join('');

            messageEl.scrollTop = messageEl.scrollHeight;
        }

        function applyThread(thread) {
            if (titleEl) titleEl.textContent = thread.qt_title || '(제목 없음)';
            const name = thread.user_name || thread.user_id || ('회원 #' + (thread.qt_u_idx || ''));
            memberLabel = thread.user_name || thread.user_id || '문의자';
            if (metaEl) {
                metaEl.textContent =
                    name + (thread.user_id ? (' · ' + thread.user_id) : '');
            }
            const closed = thread.state === 'S';
            if (statusBadge) {
                statusBadge.textContent = closed ? '종료' : '진행중';
                statusBadge.className = 'badge ' + (closed ? 'bg-secondary' : 'bg-light text-dark');
            }
            if (closeBtn) closeBtn.disabled = closed;
            if (bodyInput) {
                bodyInput.disabled = closed;
                bodyInput.placeholder = closed ? '종료된 문의입니다' : '메시지를 입력하세요…';
            }
            if (form) {
                const sendBtn = form.querySelector('#qaReplyBtn');
                if (sendBtn) sendBtn.disabled = closed;
            }
        }

        async function loadDetail() {
            const res = await fetch('/admin/qa/threads/' + idx, {
                headers: {Accept: 'application/json'},
            });
            if (!res.ok) {
                if (titleEl) titleEl.textContent = '문의를 불러올 수 없습니다.';
                if (messageEl) {
                    messageEl.innerHTML =
                        '<div class="qa-messenger-empty text-danger">상세를 불러오지 못했습니다.</div>';
                }
                return;
            }
            const data = await res.json();
            applyThread(data.thread || {});
            renderMessages(data.messages || []);
            notifyUnread(data.unread_total);
        }

        if (form) {
            form.addEventListener('submit', async function (event) {
                event.preventDefault();
                if (!bodyInput || bodyInput.disabled) return;
                const text = bodyInput.value.trim();
                if (!text) return;
                const btn = root.querySelector('#qaReplyBtn');
                if (btn) btn.disabled = true;
                try {
                    const res = await fetch('/admin/qa/threads/' + idx + '/messages', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            Accept: 'application/json',
                        },
                        body: JSON.stringify({body: text}),
                    });
                    const data = await res.json();
                    if (!res.ok) {
                        window.alert((data.detail && data.detail.message) || data.detail || '전송 실패');
                        return;
                    }
                    bodyInput.value = '';
                    bodyInput.style.height = 'auto';
                    notifyUnread(data.unread_total);
                    await loadDetail();
                } catch (error) {
                    window.alert('전송 중 오류가 발생했습니다.');
                } finally {
                    if (btn && !(bodyInput && bodyInput.disabled)) btn.disabled = false;
                }
            });

            if (bodyInput) {
                bodyInput.addEventListener('input', function () {
                    bodyInput.style.height = 'auto';
                    bodyInput.style.height = Math.min(bodyInput.scrollHeight, 120) + 'px';
                });
            }
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', async function () {
                if (!window.confirm('이 문의를 종료할까요?')) return;
                const res = await fetch('/admin/qa/threads/' + idx + '/close', {
                    method: 'POST',
                    headers: {Accept: 'application/json'},
                });
                const data = await res.json();
                if (!res.ok) {
                    window.alert((data.detail && data.detail.message) || '종료 실패');
                    return;
                }
                notifyUnread(data.unread_total);
                await loadDetail();
            });
        }

        if (window.AdminQaSSE && !root.dataset.sseBound) {
            root.dataset.sseBound = '1';
            const refreshIfMine = function (payload) {
                if (payload && payload.thread &&
                    Number(payload.thread.idx) === Number(idx)) {
                    loadDetail();
                }
            };
            window.AdminQaSSE.on('qa_message', refreshIfMine);
            window.AdminQaSSE.on('qa_closed', refreshIfMine);
        }

        loadDetail();
    }

    window.initQaDetail = initQaDetail;
})();
