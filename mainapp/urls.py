from django.urls import path, include
from . import views

urlpatterns = [
    path('student/', views.student_detail, name='student_dashboard'),
    path('tutor/', views.tutor_dashboard, name='tutor_dashboard'),
    path('accounts/', include('django.contrib.auth.urls')),
]
