from django.urls import path, include
from . import views

urlpatterns = [
    path('', views.loginPage, name='login'),
    path('logout/', views.logoutUser, name='logout'),
    path('student/', views.student_detail, name='student_dashboard'),
    path('student_csv/', views.grades_csv, name='student_csv'),
    path('tutor/', views.tutor_dashboard, name='tutor_dashboard'),
    path('student/<int:student_id>/grade/new/',
         views.create_edit_grade, name='grade_create'),
    path('student/edit/grade/<int:grade_id>/',
         views.create_edit_grade, name='grade_edit'),
    path('student/<int:student_id>/ausencia/new/',
         views.create_edit_ausencia, name='ausencia_create'),
    path('student/edit/ausencia/<int:ausencia_id>/',
         views.create_edit_ausencia, name='ausencia_edit'),
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path(
        'students/<int:student_id>/dashboard/',
        views.student_dashboard_content,
        name='student_dashboard_content'
    ),
    path('class/<int:course_id>/dashboard/',
         views.class_dashboard, name='class_dashboard'),
    path('section/<str:section>/courses/',
         views.section_courses, name='section_courses'),
    # Search endpoint for student lookups (visible to professors only)
    path('search/', views.search_students, name='search_students'),

]
