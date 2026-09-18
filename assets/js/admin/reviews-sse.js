/**
 * 풀이 이력 SSE (/admin/reviews/stream)
 */
(function () {
    'use strict';

    const SSE_URL = (typeof baseUrl === 'string' ? baseUrl : '') + '/admin/reviews/stream';
    const listeners = {
        review_created: [],
    };
    let eventSource = null;

    function parseEventData(raw) {
        if (!raw) return {};
        try {
            return JSON.parse(raw);
        } catch (error) {
            return {};
        }
    }

    function emit(event, data) {
        (listeners[event] || []).forEach(function (callback) {
            callback(data);
        });
        document.dispatchEvent(new CustomEvent('admin:review:' + event, {detail: data}));
    }

    function connect() {
        if (eventSource || typeof EventSource === 'undefined') {
            return;
        }
        eventSource = new EventSource(SSE_URL);
        eventSource.addEventListener('review_created', function (event) {
            emit('review_created', parseEventData(event.data));
        });
    }

    window.AdminReviewSSE = {
        connect: connect,
        on: function (event, callback) {
            if (!listeners[event] || typeof callback !== 'function') return;
            listeners[event].push(callback);
        },
    };

    connect();
})();
