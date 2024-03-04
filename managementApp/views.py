from django.shortcuts import render

from homeApp.utils import login_required
from utils.custom_decorators import check_groups


# Create your views here.

@check_groups('Admin', 'Owner')
def admin_home(request):
    context = {
    }
    return render(request, 'managementApp/index.html', context)

@login_required
@check_groups('Admin', 'Owner')
def manage_class(request):
    context = {
    }
    return render(request, 'managementApp/class.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_subjects(request):
    context = {
    }
    return render(request, 'managementApp/subjects/addEditListSubjects.html', context)



