import os
import sys
import django

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schoolStackPro.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth.models import User, Group

from homeApp.models import SchoolDetail
from managementApp.models import TeacherDetail, StaffAccess, StaffRole, StaffRolePermission
from managementApp.access_control import (
    MANAGEMENT_MODULES, 
    MANAGEMENT_ACTIONS, 
    ACTION_FIELD_MAP,
    has_management_permission,
    can_pass_group_gate
)

def run_granular_policy_checks():
    # 1. Fetch user & teacher profile
    user = User.objects.get(username='T86724')
    staff = TeacherDetail.objects.get(userID=user)
    school = staff.schoolID
    
    # 2. Add to Teaching group
    group, _ = Group.objects.get_or_create(name='Teaching')
    group.user_set.add(user)
    
    # 3. Create a unique role for this test
    role, _ = StaffRole.objects.get_or_create(
        schoolID=school,
        name="AllActionsTestRole",
        isDeleted=False,
        defaults={'description': 'All actions test role', 'isActive': True}
    )
    role.isActive = True
    role.save()
        
    # 4. Link staff to this role
    access, _ = StaffAccess.objects.update_or_create(
        staffID=staff,
        defaults={
            'roleID': role,
            'isManagementAccessEnabled': True,
            'notes': 'All actions granular testing'
        }
    )

    print("="*85)
    print(f"  RUNNING POLICY MATRIX CHECK: {len(MANAGEMENT_MODULES)} Modules x {len(MANAGEMENT_ACTIONS)} Actions")
    print("="*85)
    
    success_count = 0
    failure_count = 0
    detailed_results = []

    # Test each module and each action
    for module_key, module_label, _ in MANAGEMENT_MODULES:
        for action_key, action_label in MANAGEMENT_ACTIONS:
            # Step A: Grant only (module_key, action_key)
            # Set all actions of all modules to False
            for m_key, _, _ in MANAGEMENT_MODULES:
                defaults = {field: False for field in ACTION_FIELD_MAP.values()}
                if m_key == module_key:
                    action_field = ACTION_FIELD_MAP.get(action_key)
                    if action_field:
                        defaults[action_field] = True
                StaffRolePermission.objects.update_or_create(
                    roleID=role,
                    moduleKey=m_key,
                    defaults=defaults
                )
            
            # Verify that (module_key, action_key) is ALLOWED
            allowed_result = has_management_permission(user, module_key, action_key)
            
            # Verify that other actions for this module are DENIED
            others_denied = True
            for other_action, _ in MANAGEMENT_ACTIONS:
                if other_action != action_key:
                    if has_management_permission(user, module_key, other_action):
                        others_denied = False
                        
            # Verify that other modules are DENIED
            other_mods_denied = True
            for other_mod, _, _ in MANAGEMENT_MODULES:
                if other_mod != module_key:
                    for act, _ in MANAGEMENT_ACTIONS:
                        if has_management_permission(user, other_mod, act):
                            other_mods_denied = False

            # Step B: Revoke permission (set all False)
            defaults = {field: False for field in ACTION_FIELD_MAP.values()}
            StaffRolePermission.objects.update_or_create(
                roleID=role,
                moduleKey=module_key,
                defaults=defaults
            )
            
            # Verify that (module_key, action_key) is now DENIED
            revoked_result = not has_management_permission(user, module_key, action_key)
            
            check_passed = allowed_result and others_denied and other_mods_denied and revoked_result
            if check_passed:
                success_count += 1
                status_str = "SUCCESS"
            else:
                failure_count += 1
                status_str = "FAILED"
                
            detailed_results.append({
                'module': module_key,
                'action': action_key,
                'status': status_str,
                'details': f"Grant check: {allowed_result}, Isolation: {others_denied & other_mods_denied}, Revoke check: {revoked_result}"
            })
            
    print(f"Completed matrix check: {success_count} Passed, {failure_count} Failed.")
    print("="*85)
    print(f"{'Module Name':<20} | {'Action':<10} | {'Status':<10} | {'Details'}")
    print("-"*85)
    for res in detailed_results:
        # Show all failures or a clean subset
        if res['status'] == "FAILED" or success_count == len(detailed_results):
            print(f"{res['module']:<20} | {res['action']:<10} | {res['status']:<10} | {res['details']}")
    print("="*85)
    
    # Reset role permissions to safe defaults at the end
    for m_key, _, _ in MANAGEMENT_MODULES:
        defaults = {field: False for field in ACTION_FIELD_MAP.values()}
        StaffRolePermission.objects.update_or_create(
            roleID=role,
            moduleKey=m_key,
            defaults=defaults
        )

if __name__ == '__main__':
    run_granular_policy_checks()
