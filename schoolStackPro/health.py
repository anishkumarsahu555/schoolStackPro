"""Minimal readiness response without exposing database or exception details."""
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@require_safe
@never_cache
def livez(request):
    """Confirm the HTTP process can respond without querying dependencies."""
    return JsonResponse({'status': 'ok'})


@require_safe
@never_cache
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            if cursor.fetchone() != (1,):
                return JsonResponse({'status': 'unavailable'}, status=503)
    except DatabaseError:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
