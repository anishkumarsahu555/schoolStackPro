from django.urls import path
from .views import *

urlpatterns = [
    path('', login_page, name='login_page'),
    path('manifest.webmanifest', dynamic_manifest, name='dynamic_manifest'),
    path('pwa/icon-<int:size>.png', dynamic_app_icon, name='dynamic_app_icon'),
    path('pwa/startup-<int:width>x<int:height>.png', dynamic_ios_startup_image, name='dynamic_ios_startup_image'),
    path('serviceworker.js', service_worker, name='service_worker'),
    path('logout/', user_logout, name='user_logout'),
    path('post_login/', post_login, name='post_login'),
    path('forgot-password/', forgot_password, name='forgot_password'),
    path('forgot-password/sent/', forgot_password_sent, name='forgot_password_sent'),
    path('forgot-password/send/', send_password_reset_link, name='send_password_reset_link'),
    path('reset-password/<uidb64>/<token>/', reset_password, name='reset_password'),
    path('quick-access/<str:target_type>/<int:target_id>/generate/', generate_access_link, name='generate_access_link'),
    path('access/<str:token>/', access_link_login, name='access_link_login'),
    path('home/', homepage, name='homepage'),
    path('profile/', profile_page, name='profile_page'),
    path('profile/update-email/', update_email, name='update_email'),
    path('change-password/', change_password, name='change_password'),

    #admin
    path('admin_home/', admin_home, name='admin_home'),
    path('manage-class/', manage_class, name='manage_class'),
    # path('add_teacher/', add_teacher, name='add_teacher'),

    ]
