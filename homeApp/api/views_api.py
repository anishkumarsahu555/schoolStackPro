from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import *
from managementApp.signals import pre_save_with_user
from utils.custom_response import SuccessResponse, ErrorResponse


def _api_response(payload, safe=False, status=200):
    if isinstance(payload, dict):
        response_type = payload.get("status")
        message = payload.get("message")
        data = payload.get("data")
        extra = {k: v for k, v in payload.items() if k not in {"status", "message", "data"}}

        if response_type == "success":
            return SuccessResponse(
                message or "Request processed successfully.",
                status_code=status,
                data=data,
                extra=extra,
            ).to_json_response()
        if response_type == "error":
            return ErrorResponse(
                message or "Request failed.",
                status_code=status,
                data=data,
                extra=extra,
            ).to_json_response()

    return DjangoJsonResponse(payload, safe=safe, status=status)


@transaction.atomic
@csrf_exempt
@login_required
def change_session(request):
    if request.method == 'POST':
        try:
            sessionID = request.POST.get("sessionID")
            instance = SchoolSession.objects.get(pk=int(sessionID),isDeleted=False)
            request.session['current_session'] = {'currentSessionYear': instance.sessionYear, 'Id': instance.pk}
            return _api_response(
                {'status': 'success', 'message': 'Session changed successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
