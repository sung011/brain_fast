/**
 * 팝업 목록 SSE (/admin/popups/stream)
 */
(function () {
    'use strict';

    const SSE_URL = (typeof baseUrl === 'string' ? baseUrl : '') + '/admin/popups/stream';
    const listeners = {
        popup_created: [],
        popup_updated: [],
        popup_deleted: [],
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
        document.dispatchEvent(new CustomEvent('admin:popup:' + event, {detail: data}));
    }

    function connect() {
        if (eventSource || typeof EventSource === 'undefined') {
            return;
        }
        eventSource = new EventSource(SSE_URL);
        eventSource.addEventListener('popup_created', function (event) {
            emit('popup_created', parseEventData(event.data));
        });
        eventSource.addEventListener('popup_updated', function (event) {
            emit('popup_updated', parseEventData(event.data));
        });
        eventSource.addEventListener('popup_deleted', function (event) {
            emit('popup_deleted', parseEventData(event.data));
        });
    }

    window.AdminPopupSSE = {
        connect: connect,
        on: function (event, callback) {
            if (!listeners[event] || typeof callback !== 'function') return;
            listeners[event].push(callback);
        },
    };

    connect();
})();
