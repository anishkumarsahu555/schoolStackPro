from django.urls import path
from .views import *

urlpatterns = [
    path('', login_page, name='login_page'),
    path('logout/', user_logout, name='user_logout'),
    path('post_login/', post_login, name='post_login'),
    path('home/', homepage, name='homepage'),

    #admin
    path('admin_home/', admin_home, name='admin_home'),
    path('manage-class/', manage_class, name='manage_class'),

    ]
