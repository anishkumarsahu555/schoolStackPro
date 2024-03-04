from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import *
from managementApp.signals import pre_save_with_user


@transaction.atomic
@csrf_exempt
@login_required
def change_session(request):
    if request.method == 'POST':
        try:
            sessionID = request.POST.get("sessionID")
            instance = SchoolSession.objects.get(pk=int(sessionID),isDeleted=False)
            request.session['current_session'] = {'currentSessionYear': instance.sessionYear, 'Id': instance.pk}
            return JsonResponse(
                {'status': 'success', 'message': 'Session changed successfully.', 'color': 'success'},
                safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)

