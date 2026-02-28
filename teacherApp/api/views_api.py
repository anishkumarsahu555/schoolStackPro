from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import Student, TeacherDetail, AssignSubjectsToTeacher, Event, StudentFee, Standard
from utils.conts import MONTHS_LIST
from utils.custom_decorators import check_groups


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherStudentsListJson(BaseDatatableView):
    order_columns = ['name', 'roll', 'standardID__name', 'gender', 'phoneNumber', 'email', 'action']

    def get_initial_queryset(self):
        queryset = Student.objects.select_related('standardID').filter(
            isDeleted=False,
        )

        current_session = self.request.session.get('current_session', {})
        current_session_id = current_session.get('Id')
        if current_session_id:
            queryset = queryset.filter(sessionID_id=current_session_id)

        selected_standard = self.request.GET.get('standard', '').strip()
        if not selected_standard.isdigit():
            return queryset.none()

        queryset = queryset.filter(standardID_id=int(selected_standard))

        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(gender__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(email__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            class_name = 'N/A'
            if item.standardID:
                class_name = item.standardID.name or 'N/A'
                if item.standardID.section:
                    class_name = f'{class_name} - {item.standardID.section}'

            view_url = reverse('teacherApp:teacher_student_detail', kwargs={'id': item.pk})
            action = (
                f'<a href="{view_url}" data-inverted="" data-tooltip="View Detail" '
                f'data-position="left center" data-variation="mini" style="font-size:10px;" '
                f'class="ui circular facebook icon button purple">'
                f'<i class="eye icon"></i></a>'
            )

            data.append([
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                escape(class_name),
                escape(item.gender or 'N/A'),
                escape(item.phoneNumber or 'N/A'),
                escape(item.email or 'N/A'),
                action,
            ])

        return data


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherAssignedSubjectsListJson(BaseDatatableView):
    order_columns = ['assignedSubjectID__standardID__name', 'assignedSubjectID__standardID__section',
                     'assignedSubjectID__subjectID__name', 'subjectBranch', 'datetime']

    def get_initial_queryset(self):
        teacher = TeacherDetail.objects.filter(
            userID_id=self.request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if not teacher:
            return AssignSubjectsToTeacher.objects.none()

        queryset = AssignSubjectsToTeacher.objects.select_related(
            'assignedSubjectID',
            'assignedSubjectID__standardID',
            'assignedSubjectID__subjectID',
        ).filter(
            isDeleted=False,
            teacherID_id=teacher.id,
            assignedSubjectID__isDeleted=False,
            assignedSubjectID__standardID__isDeleted=False,
            assignedSubjectID__subjectID__isDeleted=False,
        )

        current_session = self.request.session.get('current_session', {})
        current_session_id = current_session.get('Id') or teacher.sessionID_id
        if current_session_id:
            queryset = queryset.filter(sessionID_id=current_session_id)

        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(assignedSubjectID__standardID__name__icontains=search)
                | Q(assignedSubjectID__standardID__section__icontains=search)
                | Q(assignedSubjectID__subjectID__name__icontains=search)
                | Q(subjectBranch__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            class_name = item.assignedSubjectID.standardID.name or 'N/A'
            section = item.assignedSubjectID.standardID.section or 'N/A'
            subject_name = item.assignedSubjectID.subjectID.name or 'N/A'
            branch = item.subjectBranch or 'N/A'
            added_on = item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'

            data.append([
                escape(class_name),
                escape(section),
                escape(subject_name),
                escape(branch),
                escape(added_on),
            ])
        return data


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherEventListJson(BaseDatatableView):
    order_columns = ['title', 'startDate', 'endDate', 'message', 'datetime']

    def get_initial_queryset(self):
        queryset = Event.objects.filter(isDeleted=False)
        current_session = self.request.session.get('current_session', {})
        current_session_id = current_session.get('Id')
        if current_session_id:
            queryset = queryset.filter(sessionID_id=current_session_id)
        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(startDate__icontains=search)
                | Q(endDate__icontains=search)
                | Q(message__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            data.append([
                escape(item.title or 'N/A'),
                escape(item.startDate.strftime('%d-%m-%Y') if item.startDate else 'N/A'),
                escape(item.endDate.strftime('%d-%m-%Y') if item.endDate else 'N/A'),
                escape(item.message or 'N/A'),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'),
            ])
        return data


@login_required
@check_groups('Teaching')
def get_assigned_class_list_api(request):
    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return JsonResponse({'success': True, 'data': []}, safe=False)
    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id

    classes = Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        id__in=assigned_class_ids,
    ).order_by('name', 'section')

    data = []
    for std in classes:
        std_name = std.name or 'N/A'
        if std.section:
            std_name = f'{std_name} - {std.section}'
        data.append({'ID': std.pk, 'Name': std_name})

    return JsonResponse({'success': True, 'data': data}, safe=False)


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherAssignedClassFeeListJson(BaseDatatableView):
    order_columns = ['name', 'roll', 'standardID__name', 'admissionFee', 'tuitionFee', 'miscFee', 'totalFee']

    def get_initial_queryset(self):
        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher:
            return Student.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id

        queryset = Student.objects.select_related('standardID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id__in=assigned_class_ids,
        )

        selected_standard = self.request.GET.get('standard', '').strip()
        if not selected_standard.isdigit():
            return queryset.none()

        selected_standard_id = int(selected_standard)
        if selected_standard_id not in set(assigned_class_ids):
            return queryset.none()

        return queryset.filter(standardID_id=selected_standard_id)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(admissionFee__icontains=search)
                | Q(tuitionFee__icontains=search)
                | Q(miscFee__icontains=search)
                | Q(totalFee__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        current_session_id = self.request.session.get('current_session', {}).get('Id')
        data = []
        for item in qs:
            class_name = 'N/A'
            if item.standardID:
                class_name = item.standardID.name or 'N/A'
                if item.standardID.section:
                    class_name = f'{class_name} - {item.standardID.section}'

            fee_qs = StudentFee.objects.filter(
                isDeleted=False,
                studentID_id=item.pk,
            )
            if current_session_id:
                fee_qs = fee_qs.filter(sessionID_id=current_session_id)

            paid_amount = fee_qs.filter(isPaid=True).aggregate(total=Sum('amount')).get('total') or 0
            paid_months = fee_qs.filter(isPaid=True).values_list('month', flat=True).distinct().count()

            total_fee = item.totalFee or 0
            pending_amount = total_fee - paid_amount
            if pending_amount < 0:
                pending_amount = 0

            view_url = reverse('teacherApp:teacher_student_detail', kwargs={'id': item.pk})
            action = (
                f'<a href="{view_url}" data-inverted="" data-tooltip="View Detail" '
                f'data-position="left center" data-variation="mini" style="font-size:10px;" '
                f'class="ui circular facebook icon button purple">'
                f'<i class="eye icon"></i></a>'
            )

            data.append([
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                escape(class_name),
                escape(f'₹{(item.admissionFee or 0):.2f}'),
                escape(f'₹{(item.tuitionFee or 0):.2f}'),
                escape(f'₹{(item.miscFee or 0):.2f}'),
                escape(f'₹{total_fee:.2f}'),
                escape(f'₹{paid_amount:.2f}'),
                escape(f'₹{pending_amount:.2f}'),
                escape(str(paid_months)),
                action,
            ])

        return data


def _get_teacher_and_assigned_class_ids(request):
    teacher = TeacherDetail.objects.filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()
    if not teacher:
        return None, []

    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
    class_ids = list(Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        classTeacher_id=teacher.id,
    ).values_list('id', flat=True))
    return teacher, class_ids


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherAssignedClassStudentsJson(BaseDatatableView):
    order_columns = ['name', 'roll', 'standardID__name', 'gender', 'phoneNumber', 'email']

    def get_initial_queryset(self):
        standard = self.request.GET.get("standard")
        if not standard or not standard.isdigit():
            return Student.objects.none()

        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher or int(standard) not in assigned_class_ids:
            return Student.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
        return Student.objects.select_related('standardID').filter(
            isDeleted=False,
            standardID_id=int(standard),
            sessionID_id=current_session_id
        ).order_by('roll')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(email__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            class_name = 'N/A'
            if item.standardID:
                class_name = item.standardID.name or 'N/A'
                if item.standardID.section:
                    class_name = f'{class_name} - {item.standardID.section}'

            student_name_js = (item.name or 'Student').replace("'", "\\'")
            action = (
                f'<button class="ui mini blue button" onclick="viewFeeDetail({item.pk},{item.standardID_id},\'{student_name_js}\')">'
                f'<i class="eye icon"></i>View Fee Detail</button>'
            )

            data.append([
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                escape(class_name),
                escape(item.gender or 'N/A'),
                escape(item.phoneNumber or 'N/A'),
                escape(item.email or 'N/A'),
                action,
            ])
        return data


@login_required
@check_groups('Teaching')
def get_assigned_student_list_by_class_api(request):
    standard = request.GET.get("standard")
    if not standard or not standard.isdigit():
        return JsonResponse({'success': True, 'data': []}, safe=False)

    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher or int(standard) not in assigned_class_ids:
        return JsonResponse({'success': True, 'data': []}, safe=False)

    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
    students = Student.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=int(standard),
    ).order_by('name')

    data = []
    for s in students:
        name = s.name or 'N/A'
        if s.roll:
            name = f"{name} - Roll {s.roll}"
        data.append({'ID': s.pk, 'Name': name})

    return JsonResponse({'success': True, 'data': data}, safe=False)


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherStudentFeeDetailsByClassJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    def get_initial_queryset(self):
        standard = self.request.GET.get("standard")
        if not standard or not standard.isdigit():
            return Student.objects.none()

        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher or int(standard) not in assigned_class_ids:
            return Student.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
        return Student.objects.select_related('standardID').filter(
            isDeleted=False,
            standardID_id=int(standard),
            sessionID_id=current_session_id
        ).order_by('roll')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        current_session_id = self.request.session.get("current_session", {}).get("Id")
        json_data = []
        for item in qs:
            month_status = {month: 'Due' for month in MONTHS_LIST}
            paid_months = StudentFee.objects.filter(
                studentID_id=item.id,
                isDeleted=False,
                isPaid=True,
                sessionID_id=current_session_id
            ).values_list('month', flat=True)
            for month in paid_months:
                if month in month_status:
                    month_status[month] = 'Paid'

            image_html = '<i class="user circle icon"></i>'
            if item.photo:
                image_html = '<img class="ui avatar image" src="{}">'.format(item.photo.thumbnail.url)

            json_data.append([
                image_html,
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                month_status['January'],
                month_status['February'],
                month_status['March'],
                month_status['April'],
                month_status['May'],
                month_status['June'],
                month_status['July'],
                month_status['August'],
                month_status['September'],
                month_status['October'],
                month_status['November'],
                month_status['December'],
            ])
        return json_data


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherStudentFeeDetailsByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note']

    def get_initial_queryset(self):
        standard = self.request.GET.get("standardByStudent")
        student = self.request.GET.get("student")
        if not standard or not student or not standard.isdigit() or not student.isdigit():
            return StudentFee.objects.none()

        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher or int(standard) not in assigned_class_ids:
            return StudentFee.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
        student_exists = Student.objects.filter(
            pk=int(student),
            isDeleted=False,
            standardID_id=int(standard),
            sessionID_id=current_session_id,
        ).exists()
        if not student_exists:
            return StudentFee.objects.none()

        return StudentFee.objects.filter(
            isDeleted=False,
            studentID_id=int(student),
            standardID_id=int(standard),
            sessionID_id=current_session_id
        ).order_by('id')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search) | Q(note__icontains=search)
                | Q(amount__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isPaid:
                status = 'Paid'
                pay_date = item.payDate.strftime('%d-%m-%Y') if item.payDate else 'N/A'
            else:
                status = 'Due'
                pay_date = 'N/A'

            json_data.append([
                escape(item.month or 'N/A'),
                status,
                pay_date,
                escape(item.amount),
                escape(item.note or ''),
            ])
        return json_data
