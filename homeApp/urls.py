from django.urls import path
from .views import *

urlpatterns = [
    path('', login_page, name='login_page'),
    path('manifest.webmanifest', dynamic_manifest, name='dynamic_manifest'),
    path('logout/', user_logout, name='user_logout'),
    path('post_login/', post_login, name='post_login'),
    path('home/', homepage, name='homepage'),
    path('profile/', profile_page, name='profile_page'),
    path('change-password/', change_password, name='change_password'),

    #admin
    path('admin_home/', admin_home, name='admin_home'),
    path('manage-class/', manage_class, name='manage_class'),
    # path('add_teacher/', add_teacher, name='add_teacher'),

    ]
