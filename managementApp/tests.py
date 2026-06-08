from datetime import date

from django.contrib.auth.models import User
from django.urls import reverse
from django.test import TestCase

from homeApp.models import SchoolDetail, SchoolSession
from managementApp.holiday_utils import holiday_audiences, resync_holidays_for_scope
from managementApp.access_control import (
    API_PERMISSION_MAP,
    APP_API_PERMISSION_MAP,
    APP_URL_PERMISSION_MAP,
    URL_PERMISSION_MAP,
    ensure_staff_login_group,
)
from managementApp.urls import urlpatterns as management_urlpatterns
from managementApp.api.urls_api import urlpatterns as management_api_urlpatterns
from certificateApp.api.urls_api import urlpatterns as certificate_api_urlpatterns
from certificateApp.urls import urlpatterns as certificate_urlpatterns
from chatApp.models import ChatMessage, ChatParticipant, ChatRoom
from chatApp.urls import urlpatterns as chat_urlpatterns
from hostelApp.api.urls_api import urlpatterns as hostel_api_urlpatterns
from hostelApp.models import HostelBuilding
from hostelApp.urls import urlpatterns as hostel_urlpatterns
from libraryApp.api.urls_api import urlpatterns as library_api_urlpatterns
from libraryApp.models import LibraryCategory, LibraryFine, LibraryMember
from libraryApp.urls import urlpatterns as library_urlpatterns
from managementApp.models import (
    SchoolHoliday,
    StaffAccess,
    StaffRole,
    StaffRolePermission,
    Standard,
    Student,
    StudentAttendance,
    TeacherAttendance,
    TeacherDetail,
)
from transportApp.api.urls_api import urlpatterns as transport_api_urlpatterns
from transportApp.models import TransportRoute
from transportApp.urls import urlpatterns as transport_urlpatterns


class StaffAccessPermissionTests(TestCase):
    password = 'QaPass123!'

    def setUp(self):
        self.school = SchoolDetail.objects.create(
            schoolName='Permission QA School',
            address='Permission QA Address',
        )
        self.session = SchoolSession.objects.create(
            schoolID=self.school,
            sessionYear='2026-2027',
            isCurrent=True,
        )

    def create_staff_user(self, username, permissions):
        user = User.objects.create_user(username=username, password=self.password)
        staff = TeacherDetail.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            userID=user,
            name=username.replace('_', ' ').title(),
            username=username,
            password=self.password,
            staffType='Office',
        )
        role = StaffRole.objects.create(
            schoolID=self.school,
            name=f'{username} Role',
            description='Test role',
            isActive=True,
        )
        action_fields = {
            'view': 'canView',
            'add': 'canAdd',
            'edit': 'canEdit',
            'delete': 'canDelete',
            'approve': 'canApprove',
            'report': 'canReport',
        }
        for module_key, actions in permissions.items():
            values = {field: False for field in action_fields.values()}
            for action in actions:
                values[action_fields[action]] = True
            StaffRolePermission.objects.create(
                roleID=role,
                moduleKey=module_key,
                **values,
            )
        StaffAccess.objects.create(
            staffID=staff,
            roleID=role,
            isManagementAccessEnabled=True,
        )
        ensure_staff_login_group(user)
        return user, role

    def create_chat_room_for_user(self, user, participant_role=ChatParticipant.ROLE_ADMIN, room_type=ChatRoom.ROOM_TYPE_DIRECT):
        other_user = User.objects.create_user(username=f'other_{user.username}', password=self.password)
        room = ChatRoom.objects.create(
            title=f'{user.username} QA Room',
            roomType=room_type,
            schoolID=self.school,
            sessionID=self.session,
            createdBy=user,
        )
        ChatParticipant.objects.create(
            roomID=room,
            userID=user,
            role=participant_role,
            canPost=True,
        )
        ChatParticipant.objects.create(
            roomID=room,
            userID=other_user,
            role=ChatParticipant.ROLE_TEACHER,
            canPost=True,
        )
        own_message = ChatMessage.objects.create(roomID=room, senderID=user, body='Own QA message')
        other_message = ChatMessage.objects.create(roomID=room, senderID=other_user, body='Other QA message')
        return room, own_message, other_message

    def chat_message_area(self, response):
        html = response.content.decode()
        return html.split('id="chatMessageList"', 1)[1].split('id="typingIndicator"', 1)[0]

    def test_staff_access_login_routes_to_management_home(self):
        self.create_staff_user('qa_view_staff', {'dashboard': ['view']})

        response = self.client.post(
            reverse('homeApp:post_login'),
            {'userName': 'qa_view_staff', 'password': self.password},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'message': 'success', 'data': '/home/'})

    def test_page_permissions_allow_and_block_by_action(self):
        user, role = self.create_staff_user(
            'qa_page_staff',
            {
                'dashboard': ['view'],
                'students': ['view'],
                'finance': ['view', 'report'],
                'access_control': ['view', 'edit'],
            },
        )
        self.client.force_login(user)

        allowed_urls = [
            reverse('managementApp:student_list'),
            reverse('managementApp:finance_dashboard'),
            reverse('managementApp:finance_reports'),
            reverse('managementApp:manage_staff_access'),
            reverse('managementApp:edit_staff_role', args=[role.id]),
        ]
        blocked_urls = [
            reverse('managementApp:add_student'),
            reverse('managementApp:student_id_cards'),
            reverse('managementApp:finance_settings'),
            reverse('managementApp:manage_session_import'),
        ]

        for url in allowed_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        for url in blocked_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertContains(response, 'Permission needed', status_code=403)
                self.assertContains(response, 'Required permission:', status_code=403)

    def test_api_permissions_return_json_403_for_restricted_actions(self):
        user, _role = self.create_staff_user(
            'qa_api_staff',
            {
                'dashboard': ['view'],
                'students': ['view'],
            },
        )
        self.client.force_login(user)

        list_response = self.client.get(reverse('managementAppAPI:StudentListJson'))
        self.assertEqual(list_response.status_code, 200)

        delete_response = self.client.post(
            reverse('managementAppAPI:delete_student'),
            {'dataID': '999999'},
        )
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(
            delete_response.json()['message'],
            'You need Delete permission for Students to continue.',
        )
        self.assertEqual(delete_response.json()['permission'], {
            'module': 'students',
            'moduleLabel': 'Students',
            'action': 'delete',
            'actionLabel': 'Delete',
        })

        add_response = self.client.post(reverse('managementAppAPI:add_student_api'), {})
        self.assertEqual(add_response.status_code, 403)
        self.assertEqual(
            add_response.json()['message'],
            'You need Add permission for Students to continue.',
        )
        self.assertEqual(add_response.json()['permission'], {
            'module': 'students',
            'moduleLabel': 'Students',
            'action': 'add',
            'actionLabel': 'Add',
        })

    def test_sidebar_hides_links_without_matching_permissions(self):
        user, _role = self.create_staff_user(
            'qa_access_staff',
            {
                'dashboard': ['view'],
                'access_control': ['view', 'edit'],
                'staff': ['view'],
            },
        )
        self.client.force_login(user)

        response = self.client.get(reverse('managementApp:manage_staff_access'))
        sidebar_html = response.content.decode().split('id="sidebar"', 1)[1].split('<div class="item">', 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn('Staff Access', sidebar_html)
        self.assertNotIn(reverse('managementApp:school_detail'), sidebar_html)
        self.assertNotIn(reverse('managementApp:manage_session_import'), sidebar_html)
        self.assertNotIn(reverse('managementApp:add_teacher'), sidebar_html)

    def test_all_management_api_routes_have_explicit_permission_mapping(self):
        api_names = {
            pattern.name
            for pattern in management_api_urlpatterns
            if getattr(pattern, 'name', None)
        }

        self.assertEqual(api_names - set(API_PERMISSION_MAP), set())

    def test_all_management_page_routes_have_explicit_permission_mapping(self):
        page_names = {
            pattern.name
            for pattern in management_urlpatterns
            if getattr(pattern, 'name', None)
        }

        self.assertEqual(page_names - set(URL_PERMISSION_MAP), set())

    def test_protected_app_routes_have_explicit_permission_mapping(self):
        route_sets = {
            'certificateApp': certificate_urlpatterns,
            'libraryApp': library_urlpatterns,
            'transportApp': transport_urlpatterns,
            'hostelApp': hostel_urlpatterns,
        }
        api_route_sets = {
            'certificateAppAPI': certificate_api_urlpatterns,
            'libraryAppAPI': library_api_urlpatterns,
            'transportAppAPI': transport_api_urlpatterns,
            'hostelAppAPI': hostel_api_urlpatterns,
        }

        for namespace, patterns in route_sets.items():
            with self.subTest(namespace=namespace):
                names = {pattern.name for pattern in patterns if getattr(pattern, 'name', None)}
                self.assertEqual(names - set(APP_URL_PERMISSION_MAP[namespace]), set())

        for namespace, patterns in api_route_sets.items():
            with self.subTest(namespace=namespace):
                names = {pattern.name for pattern in patterns if getattr(pattern, 'name', None)}
                self.assertEqual(names - set(APP_API_PERMISSION_MAP[namespace]), set())

        with self.subTest(namespace='chatApp'):
            names = {pattern.name for pattern in chat_urlpatterns if getattr(pattern, 'name', None)}
            self.assertEqual(names - set(APP_API_PERMISSION_MAP['chatApp']), set())

    def test_certificate_permissions_are_action_specific(self):
        user, _role = self.create_staff_user(
            'qa_certificate_staff',
            {
                'dashboard': ['view'],
                'certificates': ['view'],
            },
        )
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse('certificateApp:dashboard')).status_code, 200)
        generate_response = self.client.get(reverse('certificateApp:generator'))
        self.assertEqual(generate_response.status_code, 403)
        self.assertContains(generate_response, 'You need Add permission for Certificates', status_code=403)

        meta_response = self.client.get(reverse('certificateAppAPI:get_certificate_generator_meta_api'))
        self.assertNotEqual(meta_response.status_code, 403)

        create_response = self.client.post(reverse('certificateAppAPI:create_certificate_issue_api'), {})
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(create_response.json()['permission']['action'], 'add')

    def test_library_permissions_return_json_for_nested_api_prefix(self):
        user, _role = self.create_staff_user(
            'qa_library_staff',
            {
                'dashboard': ['view'],
                'library': ['view'],
            },
        )
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse('libraryApp:dashboard')).status_code, 200)
        reports_response = self.client.get(reverse('libraryApp:reports'))
        self.assertEqual(reports_response.status_code, 403)
        self.assertContains(reports_response, 'You need Report permission for Library', status_code=403)

        summary_response = self.client.get(reverse('libraryAppAPI:dashboard_summary'))
        self.assertEqual(summary_response.status_code, 200)

        delete_response = self.client.post(reverse('libraryAppAPI:delete_api'), {'type': 'book', 'id': '999999'})
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(delete_response['content-type'], 'application/json')
        self.assertEqual(delete_response.json()['permission']['action'], 'delete')

        report_response = self.client.get(reverse('libraryAppAPI:library_report_csv'))
        self.assertEqual(report_response.status_code, 403)
        self.assertEqual(report_response['content-type'], 'application/json')
        self.assertEqual(report_response.json()['permission']['action'], 'report')

    def test_communication_permissions_protect_chat_routes(self):
        no_chat_user, _role = self.create_staff_user(
            'qa_no_chat_staff',
            {'dashboard': ['view']},
        )
        self.client.force_login(no_chat_user)

        inbox_response = self.client.get(reverse('chatApp:inbox'))
        self.assertEqual(inbox_response.status_code, 403)
        self.assertContains(inbox_response, 'You need View permission for Communication', status_code=403)

        view_user, _role = self.create_staff_user(
            'qa_chat_view_staff',
            {'dashboard': ['view'], 'communication': ['view']},
        )
        self.client.force_login(view_user)

        self.assertEqual(self.client.get(reverse('chatApp:inbox')).status_code, 200)
        unread_response = self.client.get(reverse('chatApp:unread_summary_api'))
        self.assertEqual(unread_response.status_code, 200)

        send_response = self.client.post(reverse('chatApp:send_message_api', args=[999999]), {'body': 'Hello'})
        self.assertEqual(send_response.status_code, 403)
        self.assertEqual(send_response.json()['permission']['action'], 'add')

        delete_response = self.client.post(reverse('chatApp:delete_message_api', args=[999999, 999999]))
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(delete_response.json()['permission']['action'], 'delete')

        moderation_response = self.client.get(reverse('chatApp:moderation'))
        self.assertEqual(moderation_response.status_code, 403)
        self.assertContains(moderation_response, 'You need Approve permission for Communication', status_code=403)

        approve_user, _role = self.create_staff_user(
            'qa_chat_approve_staff',
            {'dashboard': ['view'], 'communication': ['view', 'approve']},
        )
        self.client.force_login(approve_user)
        self.assertEqual(self.client.get(reverse('chatApp:moderation')).status_code, 200)

        report_user, _role = self.create_staff_user(
            'qa_chat_report_staff',
            {'dashboard': ['view'], 'communication': ['view', 'report']},
        )
        self.client.force_login(report_user)
        export_response = self.client.get(reverse('chatApp:moderation_export'))
        self.assertEqual(export_response.status_code, 200)
        self.assertEqual(export_response['content-type'], 'text/csv')

    def test_communication_permission_works_without_legacy_teaching_group(self):
        user, _role = self.create_staff_user(
            'qa_chat_no_legacy_group',
            {'dashboard': ['view'], 'communication': ['view']},
        )
        user.groups.clear()
        self.client.force_login(user)

        response = self.client.get(reverse('chatApp:inbox'))
        self.assertEqual(response.status_code, 200)

    def test_chat_action_specific_roles_get_only_matching_controls(self):
        cases = [
            (
                'view',
                [],
                ['Type your message', 'data-message-reply', 'data-message-edit', 'data-message-delete', 'data-message-pin', 'Report', 'Export'],
            ),
            (
                'add',
                ['Type your message', 'data-message-reply', 'data-message-save', 'data-message-forward'],
                ['data-message-edit', 'data-message-delete', 'data-message-pin', 'Report', 'Export'],
            ),
            (
                'edit',
                ['data-message-edit'],
                ['Type your message', 'data-message-delete', 'data-message-pin', 'Report', 'Export'],
            ),
            (
                'delete',
                ['data-message-delete'],
                ['Type your message', 'data-message-edit', 'data-message-pin', 'Report', 'Export'],
            ),
            (
                'approve',
                ['data-message-pin', 'roomSettingsToggle'],
                ['Type your message', 'data-message-edit', 'data-message-delete', 'Report', 'Export'],
            ),
            (
                'report',
                ['Report', 'Export'],
                ['Type your message', 'data-message-edit', 'data-message-delete', 'data-message-pin'],
            ),
        ]

        for action, expected, blocked in cases:
            with self.subTest(action=action):
                user, _role = self.create_staff_user(
                    f'qa_chat_{action}_controls',
                    {'dashboard': ['view'], 'communication': ['view', action]},
                )
                room_type = ChatRoom.ROOM_TYPE_CLASS if action == 'approve' else ChatRoom.ROOM_TYPE_DIRECT
                room, _own_message, _other_message = self.create_chat_room_for_user(user, room_type=room_type)
                self.client.force_login(user)

                response = self.client.get(reverse('chatApp:room', args=[room.id]))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                message_area = self.chat_message_area(response)

                for text in expected:
                    source = html if text in {'Type your message', 'roomSettingsToggle', 'Export'} else message_area
                    self.assertIn(text, source)
                for text in blocked:
                    source = html if text in {'Type your message', 'roomSettingsToggle', 'Export'} else message_area
                    self.assertNotIn(text, source)

    def test_chat_page_post_actions_require_matching_permissions(self):
        user, _role = self.create_staff_user(
            'qa_chat_post_action_staff',
            {'dashboard': ['view'], 'communication': ['view', 'add']},
        )
        room, own_message, other_message = self.create_chat_room_for_user(user)
        self.client.force_login(user)

        edit_response = self.client.post(
            reverse('chatApp:room', args=[room.id]),
            {'action': 'edit_message', 'message_id': own_message.id, 'body': 'Updated'},
        )
        self.assertEqual(edit_response.status_code, 403)
        self.assertContains(edit_response, 'You need Edit permission for Communication', status_code=403)

        report_response = self.client.post(
            reverse('chatApp:room', args=[room.id]),
            {'action': 'report_message', 'message_id': other_message.id, 'reason': 'QA report'},
        )
        self.assertEqual(report_response.status_code, 403)
        self.assertContains(report_response, 'You need Report permission for Communication', status_code=403)

        send_response = self.client.post(
            reverse('chatApp:room', args=[room.id]),
            {'action': 'send_message', 'body': 'Allowed add message'},
        )
        self.assertEqual(send_response.status_code, 302)

    def test_transport_permissions_are_method_aware(self):
        user, _role = self.create_staff_user(
            'qa_transport_staff',
            {
                'dashboard': ['view'],
                'transport': ['view'],
            },
        )
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse('transportApp:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('transportAppAPI:routes_api')).status_code, 200)

        post_response = self.client.post(reverse('transportAppAPI:routes_api'), {})
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(post_response.json()['permission']['action'], 'edit')

        delete_response = self.client.post(reverse('transportAppAPI:delete_route_api'), {'id': '999999'})
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(delete_response.json()['permission']['action'], 'delete')

        report_response = self.client.get(reverse('transportApp:manage_reports'))
        self.assertEqual(report_response.status_code, 403)
        self.assertContains(report_response, 'You need Report permission for Transport', status_code=403)

    def test_hostel_permissions_are_method_aware(self):
        user, _role = self.create_staff_user(
            'qa_hostel_staff',
            {
                'dashboard': ['view'],
                'hostel': ['view'],
            },
        )
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse('hostelApp:dashboard')).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('hostelAppAPI:buildings_api')).status_code, 403)

        post_response = self.client.post(reverse('hostelAppAPI:buildings_api'), {})
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(post_response.json()['permission']['action'], 'edit')

        delete_response = self.client.post(reverse('hostelAppAPI:delete_building_api'), {'id': '999999'})
        self.assertEqual(delete_response.status_code, 403)
        self.assertEqual(delete_response.json()['permission']['action'], 'delete')

        report_response = self.client.get(reverse('hostelApp:manage_reports'))
        self.assertEqual(report_response.status_code, 403)
        self.assertContains(report_response, 'You need Report permission for Hostel', status_code=403)

    def test_view_only_roles_do_not_get_table_action_buttons(self):
        library_user, _role = self.create_staff_user(
            'qa_library_view_buttons',
            {'dashboard': ['view'], 'library': ['view']},
        )
        LibraryCategory.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Reference',
            code='REF',
        )
        self.client.force_login(library_user)
        response = self.client.get(reverse('libraryAppAPI:CategoryListJson'))
        self.assertEqual(response.status_code, 200)
        rendered = str(response.json()['data'])
        self.assertIn('View only', rendered)
        self.assertNotIn('editCategory', rendered)
        self.assertNotIn('confirmDeleteCategory', rendered)

        transport_user, _role = self.create_staff_user(
            'qa_transport_view_buttons',
            {'dashboard': ['view'], 'transport': ['view']},
        )
        TransportRoute.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            routeCode='R1',
            routeName='Route 1',
        )
        self.client.force_login(transport_user)
        response = self.client.get(reverse('transportAppAPI:RouteListJson'))
        self.assertEqual(response.status_code, 200)
        rendered = str(response.json()['data'])
        self.assertIn('viewRouteDetail', rendered)
        self.assertNotIn('editRoute', rendered)
        self.assertNotIn('confirmDeleteRoute', rendered)

        hostel_user, _role = self.create_staff_user(
            'qa_hostel_view_buttons',
            {'dashboard': ['view'], 'hostel': ['view']},
        )
        HostelBuilding.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            buildingCode='B1',
            buildingName='Block 1',
        )
        self.client.force_login(hostel_user)
        response = self.client.get(reverse('hostelAppAPI:BuildingListJson'))
        self.assertEqual(response.status_code, 200)
        rendered = str(response.json()['data'])
        self.assertIn('View only', rendered)
        self.assertNotIn('editBuilding', rendered)
        self.assertNotIn('confirmDeleteBuilding', rendered)

    def test_view_only_roles_do_not_get_page_action_buttons(self):
        library_user, _role = self.create_staff_user(
            'qa_library_view_page_buttons',
            {'dashboard': ['view'], 'library': ['view']},
        )
        self.client.force_login(library_user)
        response = self.client.get(reverse('libraryApp:manage_books'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Add Book')
        response = self.client.get(reverse('libraryApp:issue_book'))
        self.assertEqual(response.status_code, 403)

        transport_user, _role = self.create_staff_user(
            'qa_transport_view_page_buttons',
            {'dashboard': ['view'], 'transport': ['view']},
        )
        self.client.force_login(transport_user)
        response = self.client.get(reverse('transportApp:manage_routes'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Add Route')
        self.assertNotContains(response, 'Add Stop')

        hostel_user, _role = self.create_staff_user(
            'qa_hostel_view_page_buttons',
            {'dashboard': ['view'], 'hostel': ['view']},
        )
        self.client.force_login(hostel_user)
        response = self.client.get(reverse('hostelApp:manage_buildings'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Add Building')
        self.assertNotContains(response, 'Add Floor')

    def test_action_specific_roles_get_only_matching_buttons(self):
        LibraryCategory.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Action QA',
            code='AQA',
        )
        library_member_staff = TeacherDetail.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Library Fine Staff',
            username='library_fine_staff',
            staffType='Office',
        )
        library_member = LibraryMember.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            memberType='staff',
            staff=library_member_staff,
            memberCode='LQA-001',
        )
        LibraryFine.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            member=library_member,
            reason='manual',
            amount='25.00',
            status='pending',
        )
        TransportRoute.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            routeCode='BTN-R1',
            routeName='Button Route',
        )
        HostelBuilding.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            buildingCode='BTN-B1',
            buildingName='Button Block',
        )

        library_cases = [
            ('add', ['Add Book'], [], ['editCategory', 'confirmDeleteCategory']),
            ('edit', [], ['editCategory'], ['confirmDeleteCategory']),
            ('delete', [], ['confirmDeleteCategory'], ['editCategory']),
        ]
        for action, expected_page_labels, expected_table_actions, blocked_table_actions in library_cases:
            with self.subTest(module='library', action=action):
                user, _role = self.create_staff_user(
                    f'qa_library_{action}_buttons',
                    {'dashboard': ['view'], 'library': ['view', action]},
                )
                self.client.force_login(user)
                page_response = self.client.get(reverse('libraryApp:manage_books'))
                self.assertEqual(page_response.status_code, 200)
                table_response = self.client.get(reverse('libraryAppAPI:CategoryListJson'))
                self.assertEqual(table_response.status_code, 200)
                page_html = page_response.content.decode()
                table_actions = str(table_response.json()['data'])
                for text in expected_page_labels:
                    self.assertIn(text, page_html)
                if action != 'add':
                    self.assertNotIn('Add Book', page_html)
                for text in expected_table_actions:
                    self.assertIn(text, table_actions)
                for text in blocked_table_actions:
                    self.assertNotIn(text, table_actions)

        transport_cases = [
            ('add', ['Add Route', 'Add Stop'], [], ['editRoute', 'confirmDeleteRoute']),
            ('edit', [], ['editRoute'], ['confirmDeleteRoute']),
            ('delete', [], ['confirmDeleteRoute'], ['editRoute']),
        ]
        for action, expected_page_labels, expected_table_actions, blocked_table_actions in transport_cases:
            with self.subTest(module='transport', action=action):
                user, _role = self.create_staff_user(
                    f'qa_transport_{action}_buttons',
                    {'dashboard': ['view'], 'transport': ['view', action]},
                )
                self.client.force_login(user)
                page_response = self.client.get(reverse('transportApp:manage_routes'))
                self.assertEqual(page_response.status_code, 200)
                table_response = self.client.get(reverse('transportAppAPI:RouteListJson'))
                self.assertEqual(table_response.status_code, 200)
                page_html = page_response.content.decode()
                table_actions = str(table_response.json()['data'])
                for text in expected_page_labels:
                    self.assertIn(text, page_html)
                if action != 'add':
                    self.assertNotIn('Add Route', page_html)
                    self.assertNotIn('Add Stop', page_html)
                for text in expected_table_actions:
                    self.assertIn(text, table_actions)
                for text in blocked_table_actions:
                    self.assertNotIn(text, table_actions)

        hostel_cases = [
            ('add', ['Add Building', 'Add Floor'], [], ['editBuilding', 'confirmDeleteBuilding']),
            ('edit', [], ['editBuilding'], ['confirmDeleteBuilding']),
            ('delete', [], ['confirmDeleteBuilding'], ['editBuilding']),
        ]
        for action, expected_page_labels, expected_table_actions, blocked_table_actions in hostel_cases:
            with self.subTest(module='hostel', action=action):
                user, _role = self.create_staff_user(
                    f'qa_hostel_{action}_buttons',
                    {'dashboard': ['view'], 'hostel': ['view', action]},
                )
                self.client.force_login(user)
                page_response = self.client.get(reverse('hostelApp:manage_buildings'))
                self.assertEqual(page_response.status_code, 200)
                table_response = self.client.get(reverse('hostelAppAPI:BuildingListJson'))
                self.assertEqual(table_response.status_code, 200)
                page_html = page_response.content.decode()
                table_actions = str(table_response.json()['data'])
                for text in expected_page_labels:
                    self.assertIn(text, page_html)
                if action != 'add':
                    self.assertNotIn('Add Building', page_html)
                    self.assertNotIn('Add Floor', page_html)
                for text in expected_table_actions:
                    self.assertIn(text, table_actions)
                for text in blocked_table_actions:
                    self.assertNotIn(text, table_actions)

        approve_user, _role = self.create_staff_user(
            'qa_library_approve_buttons',
            {'dashboard': ['view'], 'library': ['view', 'approve']},
        )
        self.client.force_login(approve_user)
        page_response = self.client.get(reverse('libraryApp:manage_fines'))
        self.assertEqual(page_response.status_code, 200)
        table_response = self.client.get(reverse('libraryAppAPI:FineListJson'))
        self.assertEqual(table_response.status_code, 200)
        page_html = page_response.content.decode()
        table_actions = str(table_response.json()['data'])
        self.assertNotIn('Add Fine', page_html)
        self.assertIn('waiveFine', table_actions)
        self.assertNotIn('showPayFineModal', table_actions)
        self.assertNotIn('editFine', table_actions)
        self.assertNotIn('confirmDeleteFine', table_actions)

        report_checks = [
            (
                'library',
                'qa_library_report_buttons',
                reverse('libraryApp:reports'),
                reverse('libraryApp:manage_books'),
                ['Overdue CSV', 'Stock CSV', 'Fines CSV'],
                ['Add Book'],
            ),
            (
                'transport',
                'qa_transport_report_buttons',
                reverse('transportApp:manage_reports'),
                reverse('transportApp:manage_routes'),
                ['Print', 'Export CSV'],
                ['Add Route', 'Add Stop'],
            ),
            (
                'hostel',
                'qa_hostel_report_buttons',
                reverse('hostelApp:manage_reports'),
                reverse('hostelApp:manage_buildings'),
                ['Resident CSV'],
                ['Add Building', 'Add Floor'],
            ),
        ]
        for module, username, report_url, list_url, expected, blocked in report_checks:
            with self.subTest(module=module, action='report'):
                user, _role = self.create_staff_user(
                    username,
                    {'dashboard': ['view'], module: ['view', 'report']},
                )
                self.client.force_login(user)
                report_response = self.client.get(report_url)
                self.assertEqual(report_response.status_code, 200)
                list_response = self.client.get(list_url)
                self.assertEqual(list_response.status_code, 200)
                rendered = report_response.content.decode() + list_response.content.decode()
                for text in expected:
                    self.assertIn(text, rendered)
                for text in blocked:
                    self.assertNotIn(text, rendered)


class HolidayAttendanceSyncTests(TestCase):
    def setUp(self):
        self.school = SchoolDetail.objects.create(schoolName='Demo School', address='Demo Address')
        self.session = SchoolSession.objects.create(
            schoolID=self.school,
            sessionYear='2026-2027',
            startDate=date(2026, 4, 1),
            endDate=date(2027, 3, 31),
            isCurrent=True,
        )
        self.teacher = TeacherDetail.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Teacher One',
        )
        self.standard = Standard.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Class 1',
            classTeacher=self.teacher,
        )
        self.student = Student.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            standardID=self.standard,
            name='Student One',
        )

    def _resync(self, holiday):
        resync_holidays_for_scope(
            session_id=self.session.id,
            school_id=self.school.id,
            start_date=holiday.startDate,
            end_date=holiday.endDate,
            audiences=holiday_audiences(holiday.appliesTo),
        )

    def test_deleting_overlapping_general_holiday_keeps_student_only_holiday(self):
        general = SchoolHoliday.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            title='General Holiday',
            holidayType='general',
            appliesTo='both',
            startDate=date(2026, 5, 20),
            endDate=date(2026, 5, 20),
        )
        students_only = SchoolHoliday.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            title='Student Holiday',
            holidayType='general',
            appliesTo='students',
            startDate=date(2026, 5, 20),
            endDate=date(2026, 5, 20),
        )
        self._resync(general)
        self._resync(students_only)

        general.isDeleted = True
        general.save()
        resync_holidays_for_scope(
            session_id=self.session.id,
            school_id=self.school.id,
            start_date=general.startDate,
            end_date=general.endDate,
            audiences=holiday_audiences(general.appliesTo),
        )

        student_row = StudentAttendance.objects.get(
            isDeleted=False,
            sessionID=self.session,
            studentID=self.student,
            attendanceDate__date=students_only.startDate,
        )
        self.assertTrue(student_row.isHoliday)
        self.assertEqual(student_row.sourceHoliday_id, students_only.id)
        self.assertFalse(TeacherAttendance.objects.filter(isDeleted=False, sourceHoliday__isnull=False).exists())

    def test_changing_holiday_from_both_to_students_removes_teacher_holiday(self):
        holiday = SchoolHoliday.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            title='Audience Change',
            holidayType='general',
            appliesTo='both',
            startDate=date(2026, 6, 10),
            endDate=date(2026, 6, 10),
        )
        self._resync(holiday)

        old_applies_to = holiday.appliesTo
        holiday.appliesTo = 'students'
        holiday.save()
        resync_holidays_for_scope(
            session_id=self.session.id,
            school_id=self.school.id,
            start_date=holiday.startDate,
            end_date=holiday.endDate,
            audiences=holiday_audiences(old_applies_to, holiday.appliesTo),
        )

        self.assertTrue(StudentAttendance.objects.filter(isDeleted=False, sourceHoliday=holiday).exists())
        self.assertFalse(TeacherAttendance.objects.filter(isDeleted=False, sourceHoliday=holiday).exists())
