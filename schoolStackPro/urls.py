"""
URL configuration for schoolStackPro project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

handler403 = "homeApp.views.error_403"
handler404 = "homeApp.views.error_404"
handler500 = "homeApp.views.error_500"

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include(('homeApp.urls', 'homeApp'), namespace='homeApp')),
    path('api/', include(('homeApp.api.urls_api', 'homeApp'), namespace='homeAppAPI')),
    path('management/', include(('managementApp.urls', 'managementApp'), namespace='managementApp')),
    path('management/api/', include(('managementApp.api.urls_api', 'managementApp'), namespace='managementAppAPI')),
    path('teacher/', include(('teacherApp.urls', 'teacherApp'), namespace='teacherApp')),
    path('teacher/api/', include(('teacherApp.api.urls_api', 'teacherApp'), namespace='teacherAppAPI')),
    path('student/', include(('studentApp.urls', 'studentApp'), namespace='studentApp')),
    path('student/api/', include(('studentApp.api.urls_api', 'studentApp'), namespace='studentAppAPI')),
    path('', include('pwa.urls')),  # You MUST use an empty string as the URL prefix
    path('management/api/cached/', include(('managementApp.cached_api.cached_urls', 'managementApp'), namespace='managementAppCachedAPI')),

]



if settings.DEBUG:
    urlpatterns = urlpatterns + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns = urlpatterns + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
