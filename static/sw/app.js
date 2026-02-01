

if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('../../static/sw/serviceworker.js')
            .then(registration => {
                console.log('SW Registered');

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