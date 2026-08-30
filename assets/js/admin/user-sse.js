(function () {
    'use strict';

    const SSE_URL = (typeof baseUrl === 'string' ? baseUrl : '') + '/admin/users/stream';
    const listeners = {
        user_created: [],
        user_updated: [],
        user_deleted: [],
    };
    let eventSource = null;

    function parseEventData(raw) {
        if (!raw) {
            return {};
        }
        try {
            return JSON.parse(raw);
        } catch (error) {
            return {};
        }
    }

    function emit(event, data) {
        listeners[event].forEach(function (callback) {
            callback(data);
        });
        document.dispatchEvent(new CustomEvent('admin:user:' + event, {detail: data}));
    }

    function connect() {
        if (eventSource || typeof EventSource === 'undefined') {
            return;
        }

        eventSource = new EventSource(SSE_URL);

        eventSource.addEventListener('user_created', function (event) {
            emit('user_created', parseEventData(event.data));
        });

        eventSource.addEventListener('user_updated', function (event) {
            emit('user_updated', parseEventData(event.data));
        });

        eventSource.addEventListener('user_deleted', function (event) {
            emit('user_deleted', parseEventData(event.data));
        });
    }

    window.AdminUserSSE = {
        connect: connect,
        on: function (event, callback) {
            if (!listeners[event] || typeof callback !== 'function') {
                return;
            }
            listeners[event].push(callback);
        },
    };

    connect();
})();
