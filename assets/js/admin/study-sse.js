/**
 * 학습 목록 SSE (/admin/studies/stream)
 */
(function () {
    'use strict';

    const SSE_URL = (typeof baseUrl === 'string' ? baseUrl : '') + '/admin/studies/stream';
    const listeners = {
        study_created: [],
        study_updated: [],
        study_deleted: [],
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
        document.dispatchEvent(new CustomEvent('admin:study:' + event, {detail: data}));
    }

    function connect() {
        if (eventSource || typeof EventSource === 'undefined') {
            return;
        }
        eventSource = new EventSource(SSE_URL);
        eventSource.addEventListener('study_created', function (event) {
            emit('study_created', parseEventData(event.data));
        });
        eventSource.addEventListener('study_updated', function (event) {
            emit('study_updated', parseEventData(event.data));
        });
        eventSource.addEventListener('study_deleted', function (event) {
            emit('study_deleted', parseEventData(event.data));
        });
    }

    window.AdminStudySSE = {
        connect: connect,
        on: function (event, callback) {
            if (!listeners[event] || typeof callback !== 'function') return;
            listeners[event].push(callback);
        },
    };

    connect();
})();
