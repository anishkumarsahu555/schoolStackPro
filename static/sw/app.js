

if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.getRegistrations()
            .then(registrations => {
                const staleRegs = registrations.filter(reg => reg.scope && reg.scope.includes('/static/sw/'));
                return Promise.all(staleRegs.map(reg => reg.unregister()));
            })
            .then(() => navigator.serviceWorker.register('/serviceworker.js', {scope: '/'}))
            .then(registration => {
                console.log('SW Registered', registration.scope);
                window.__swRegistrationPromise = Promise.resolve(registration);

                // OPTIONAL: Check for updates automatically every time page loads
                registration.update();
            })
            .catch(err => console.log('SW Registration failed:', err));

        // AUTO-REFRESH LOGIC
        // If the SW updates the cache, we reload the page to show new content
        let refreshing = false;
        navigator.serviceWorker.addEventListener('controllerchange', () => {
            if (!refreshing) {
                window.location.reload();
                refreshing = true;
            }
        });
    });
}
