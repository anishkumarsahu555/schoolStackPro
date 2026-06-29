(function () {
    if (!('serviceWorker' in navigator)) {
        return;
    }

    var hasRefreshedForUpdate = false;

    function notify(message, type) {
        if (!message) return;
        if (typeof window.showNotificationBanner === 'function') {
            window.showNotificationBanner(message, type || 'info');
            return;
        }
        window.dispatchEvent(new CustomEvent('app:notice', {
            detail: {message: message, type: type || 'info'}
        }));
    }

    async function unregisterStaleWorkers() {
        var registrations = await navigator.serviceWorker.getRegistrations();
        var stale = registrations.filter(function (registration) {
            return registration.scope && registration.scope.indexOf('/static/sw/') !== -1;
        });
        await Promise.all(stale.map(function (registration) {
            return registration.unregister();
        }));
    }

    function postSkipWaiting(registration) {
        if (registration && registration.waiting) {
            registration.waiting.postMessage({type: 'SKIP_WAITING'});
        }
    }

    function watchForUpdates(registration) {
        if (!registration) return;

        if (registration.waiting) {
            postSkipWaiting(registration);
        }

        registration.addEventListener('updatefound', function () {
            var installingWorker = registration.installing;
            if (!installingWorker) return;

            installingWorker.addEventListener('statechange', function () {
                if (installingWorker.state !== 'installed') return;
                if (!navigator.serviceWorker.controller) return;
                notify('Updating offline files...', 'info');
                postSkipWaiting(registration);
            });
        });
    }

    function bindConnectivityNotices() {
        window.addEventListener('offline', function () {
            notify('You are offline. Some saved pages may still open.', 'warning');
        });
        window.addEventListener('online', function () {
            notify('Back online. Syncing fresh data.', 'success');
            navigator.serviceWorker.ready.then(function (registration) {
                return registration.update();
            }).catch(function () {
                // Update checks are best-effort.
            });
        });
    }

    window.addEventListener('load', function () {
        unregisterStaleWorkers()
            .then(function () {
                return navigator.serviceWorker.register('/serviceworker.js', {scope: '/'});
            })
            .then(function (registration) {
                window.__swRegistrationPromise = Promise.resolve(registration);
                watchForUpdates(registration);
                registration.update();
            })
            .catch(function () {
                window.__swRegistrationPromise = Promise.resolve(null);
            });

        navigator.serviceWorker.addEventListener('controllerchange', function () {
            if (hasRefreshedForUpdate) return;
            hasRefreshedForUpdate = true;
            window.location.reload();
        });

        navigator.serviceWorker.addEventListener('message', function (event) {
            var data = event.data || {};
            if (data.type === 'SW_ACTIVATED' && navigator.serviceWorker.controller) {
                notify('Offline support updated.', 'success');
            }
        });

        bindConnectivityNotices();
    });
})();
