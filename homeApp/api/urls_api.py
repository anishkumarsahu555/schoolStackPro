from django.urls import path
from .views_api import *


urlpatterns = [
    # api
    path('change_session', change_session, name='change_session'),
    path('get_push_public_config_api', get_push_public_config_api, name='get_push_public_config_api'),
    path('upsert_push_subscription_api', upsert_push_subscription_api, name='upsert_push_subscription_api'),
    path('disable_push_subscription_api', disable_push_subscription_api, name='disable_push_subscription_api'),
    path('send_test_push_api', send_test_push_api, name='send_test_push_api'),

    ]
