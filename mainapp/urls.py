from django.urls import path, include
from . import views

urlpatterns = [
    path('', views.loginPage, name='login'),
    path('logout/', views.logoutUser, name='logout'),
    path('student/', views.student_detail, name='student_dashboard'),
    path('student_csv/', views.grades_csv, name='student_csv'),
    path('tutor/', views.tutor_dashboard, name='tutor_dashboard'),
]
