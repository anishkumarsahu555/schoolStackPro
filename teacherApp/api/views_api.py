from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import Student, TeacherDetail, AssignSubjectsToTeacher, Event
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
