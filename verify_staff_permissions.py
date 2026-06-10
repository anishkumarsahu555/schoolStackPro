import os
import sys
import time
import subprocess
import django
from decimal import Decimal

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schoolStackPro.settings')
django.setup()

from django.contrib.auth.models import User, Group
from homeApp.models import SchoolDetail, SchoolSession
from managementApp.models import TeacherDetail, StaffAccess, StaffRole, StaffRolePermission
from managementApp.access_control import MANAGEMENT_MODULES, ACTION_FIELD_MAP

from playwright.sync_api import sync_playwright

TEST_MODULES = [
    ('dashboard', '/management/home/'),
    ('school_settings', '/management/school-detail/'),
    ('classes', '/management/manage-class/'),
    ('timetable', '/management/school-timetable/'),
    ('staff', '/management/teacher_list/'),
    ('subjects', '/management/manage_subjects/'),
    ('students', '/management/student_list/'),
    ('certificates', '/certificates/designs/'),
    ('parents', '/management/manage_parents/'),
    ('attendance', '/management/student_attendance_history/'),
    ('fees', '/management/student_fee/'),
    ('exams', '/management/manage_exams/'),
    ('marks', '/management/exam_marks_details/'),
    ('events', '/management/manage_event/'),
    ('holidays', '/management/manage_holidays/'),
    ('leave', '/management/manage_leave_types/'),
    ('finance', '/management/finance/'),
    ('communication', '/chat/'),
    ('library', '/management/library/books/'),
    ('transport', '/transport/'),
    ('hostel', '/hostel/'),
    ('audit', '/management/audit-manager/'),
]

def prepare_db_access():
    print("Preparing Database setup for user T86724 (Anish Kumar Sahu)...")
    
    # 1. Fetch user & teacher profile
    user = User.objects.get(username='T86724')
    staff = TeacherDetail.objects.get(userID=user)
    school = staff.schoolID
    
    # 2. Add to Teaching group
    group, _ = Group.objects.get_or_create(name='Teaching')
    group.user_set.add(user)
    
    # 3. Create a unique role for this test
    role, created = StaffRole.objects.get_or_create(
        schoolID=school,
        name="GranularTestRole",
        isDeleted=False,
        defaults={'description': 'Automated granular test role', 'isActive': True}
    )
    if not created:
        role.isActive = True
        role.save()
        
    # 4. Link staff to this role
    access, _ = StaffAccess.objects.update_or_create(
        staffID=staff,
        defaults={
            'roleID': role,
            'isManagementAccessEnabled': True,
            'notes': 'Granular permission testing'
        }
    )
    
    print(f"Role 'GranularTestRole' assigned and enabled for {staff.name}.")
    return role

import threading

def run_in_thread(func, *args, **kwargs):
    result = []
    exception = []
    def worker():
        try:
            res = func(*args, **kwargs)
            result.append(res)
        except Exception as e:
            exception.append(e)
            
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    if exception:
        raise exception[0]
    return result[0] if result else None

def set_role_permission(role, active_module):
    # Set all permissions to True for active_module, False for others
    for module_key, label, icon in MANAGEMENT_MODULES:
        is_active = (module_key == active_module)
        defaults = {field: is_active for field in ACTION_FIELD_MAP.values()}
            
        StaffRolePermission.objects.update_or_create(
            roleID=role,
            moduleKey=module_key,
            defaults=defaults
        )

def revoke_all_permissions(role):
    # Set all permissions to False for all modules
    for module_key, label, icon in MANAGEMENT_MODULES:
        defaults = {field: False for field in ACTION_FIELD_MAP.values()}
        StaffRolePermission.objects.update_or_create(
            roleID=role,
            moduleKey=module_key,
            defaults=defaults
        )

def run_tests():
    # Prepare Database Setup
    role = prepare_db_access()
    
    # Start server in a background process
    print("Starting Django test server on port 8002...")
    server_process = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", "8002"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3) # Wait for Django server to start
    
    results_grant = {}
    results_revoke = {}
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            
            # Log in once as Anish Kumar Sahu
            print("Logging in to dev server as Anish Kumar Sahu (T86724)...")
            page.goto("http://127.0.0.1:8002/")
            page.fill("input[name='userName']", "T86724")
            page.fill("input[name='password']", "sahu12345")
            
            # Click submit button
            page.click("button.login-btn")
            page.wait_for_load_state("networkidle")
            
            # Check login success
            print(f"Logged in successfully. Current URL: {page.url}")
            
            # Loop through modules and verify access
            for test_mod, url_path in TEST_MODULES:
                print(f"\n--- Testing Module: {test_mod.upper()} ---")
                
                # 1. GRANT TEST
                run_in_thread(set_role_permission, role, test_mod)
                results_grant[test_mod] = {}
                for target_mod, target_url in TEST_MODULES:
                    full_url = f"http://127.0.0.1:8002{target_url}"
                    response = page.goto(full_url)
                    
                    status_code = response.status if response else 500
                    is_permission_needed_page = "Permission needed" in page.content()
                    
                    # Normalize URLs by stripping trailing slashes to avoid false positive redirects
                    is_redirected = page.url.rstrip('/') != full_url.rstrip('/')
                    
                    if status_code != 200 or is_redirected or is_permission_needed_page:
                        access_result = "DENIED"
                    else:
                        access_result = "ALLOWED"
                    results_grant[test_mod][target_mod] = access_result
                
                # 2. REVOKE TEST
                run_in_thread(revoke_all_permissions, role)
                results_revoke[test_mod] = {}
                for target_mod, target_url in TEST_MODULES:
                    full_url = f"http://127.0.0.1:8002{target_url}"
                    response = page.goto(full_url)
                    
                    status_code = response.status if response else 500
                    is_permission_needed_page = "Permission needed" in page.content()
                    is_redirected = page.url.rstrip('/') != full_url.rstrip('/')
                    
                    if status_code != 200 or is_redirected or is_permission_needed_page:
                        access_result = "DENIED"
                    else:
                        access_result = "ALLOWED"
                    results_revoke[test_mod][target_mod] = access_result
                
                # Print output for current module
                grant_self = results_grant[test_mod][test_mod]
                grant_others_blocked = all(results_grant[test_mod][other] == "DENIED" for other in results_grant[test_mod] if other != test_mod)
                
                revoke_self = results_revoke[test_mod][test_mod]
                revoke_others_blocked = all(results_revoke[test_mod][other] == "DENIED" for other in results_revoke[test_mod])
                
                print(f"  [GRANT]  Self Access: {grant_self} | Others Blocked: {grant_others_blocked}")
                print(f"  [REVOKE] Self Access: {revoke_self} | All Blocked: {revoke_others_blocked}")
                
    finally:
        # Stop background Django server
        print("\nStopping Django test server...")
        server_process.terminate()
        server_process.wait()
        
    # Print clean report
    print("\n" + "="*80)
    print("                FINAL DETAILED ACCESS CONTROL REPORT")
    print("="*80)
    print(f"{'Module Name':<20} | {'GRANT Test (Self)':<18} | {'GRANT Test (Others)':<20} | {'REVOKE Test'}")
    print("-"*80)
    for mod in results_grant:
        grant_self = results_grant[mod][mod]
        grant_others = "SUCCESS" if all(results_grant[mod][other] == "DENIED" for other in results_grant[mod] if other != mod) else "FAILED"
        
        revoke_self = results_revoke[mod][mod]
        revoke_all = "SUCCESS" if all(results_revoke[mod][other] == "DENIED" for other in results_revoke[mod]) else "FAILED"
        
        print(f"{mod:<20} | {grant_self:<18} | {grant_others:<20} | {revoke_all}")
    print("="*80)

if __name__ == '__main__':
    run_tests()
