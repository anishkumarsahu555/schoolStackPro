"""Probe the local HTTP server, respecting the configured Django host allowlist."""
import os
import json
import urllib.request

def main():
    hosts = [h.strip() for h in os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]
    host = next(iter(hosts), 'localhost')
    host = 'localhost' if host == '*' else host.lstrip('.')
    port = int(os.environ.get('APP_PORT', '8000'))
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/healthz', headers={'Host': host}
    )
    # Local probes must not be sent through HTTP_PROXY/HTTPS_PROXY.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        if response.status != 200 or json.load(response) != {'status': 'ok'}:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
