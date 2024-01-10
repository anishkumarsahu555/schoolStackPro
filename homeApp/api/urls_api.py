from django.urls import path
from .views_api import *


urlpatterns = [
    # api
    path('change_session', change_session, name='change_session'),

    ]