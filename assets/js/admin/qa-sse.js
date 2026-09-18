/**
 * Q&A SSE (/admin/qa/stream)
 */
(function () {
    'use strict';

    const SSE_URL = (typeof baseUrl === 'string' ? baseUrl : '') + '/admin/qa/stream';
    const listeners = {
        qa_created: [],
        qa_message: [],
        qa_closed: [],
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
        document.dispatchEvent(new CustomEvent('admin:qa:' + event, {detail: data}));
    }

    function connect() {
        if (eventSource || typeof EventSource === 'undefined') {
            return;
        }
        eventSource = new EventSource(SSE_URL);
        Object.keys(listeners).forEach(function (eventName) {
            eventSource.addEventListener(eventName, function (event) {
                emit(eventName, parseEventData(event.data));
            });
        });
    }

    window.AdminQaSSE = {
        connect: connect,
        on: function (event, callback) {
            if (!listeners[event] || typeof callback !== 'function') return;
            listeners[event].push(callback);
        },
    };

    connect();
})();
