from django.urls import path
from .views_api import *


urlpatterns = [
    # api
    path('add_class', add_class, name='add_class'),
    path('class_list', StandardListJson.as_view(), name='class_list'),

    ]