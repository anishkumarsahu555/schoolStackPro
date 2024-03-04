from django.urls import path
from .views_api import *


urlpatterns = [
    # api
    path('add_class', add_class, name='add_class'),
    path('class_list', StandardListJson.as_view(), name='class_list'),
    path('get_class_detail', get_class_detail, name='get_class_detail'),
    path('delete_class', delete_class, name='delete_class'),

    ]