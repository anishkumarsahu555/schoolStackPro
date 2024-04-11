from django.shortcuts import render, get_object_or_404

from homeApp.utils import login_required
from managementApp.models import *
from studentApp.data_utils import StudentData
from utils.custom_decorators import check_groups


# Create your views here.

@check_groups('Student')
def student_home(request):
    context = {
    }
    return render(request, 'studentApp/dashboard.html', context)


@check_groups('Student')
def attendance_history(request):
    context = {
    }
    return render(request, 'studentApp/attendance/attendanceHistory.html', context)


@check_groups('Student')
def fee_detail(request):
    context = {
    }
    return render(request, 'studentApp/fee/feeDetails.html', context)
