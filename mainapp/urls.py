from django.urls import path, include
from . import views
from .views import create_school_year_view, create_courses_sections_view, adminage_dashboard_view, assign_subjects_view, load_course_sections, create_and_assign_student_view

urlpatterns = [
    path('adminage/create-student-class/', create_and_assign_student_view,
         name='create_and_assign_student'),

    # Mantén la URL de AJAX

    # Mantén la URL de AJAX
    path('ajax/load-sections/', load_course_sections,
         name='load_course_sections'),
    path('adminage/assign-subjects/',
         assign_subjects_view, name='assign_subjects'),

    # 0. DASHBOARD PRINCIPAL (PUNTO DE ENTRADA)
    path('adminage/', adminage_dashboard_view, name='adminage_dashboard'),

    # 1. FLUJO PASO 1: Crear Año Escolar
    path('adminage/create-school-year/',
         create_school_year_view, name='create_school_year'),

    # 2. FLUJO PASO 2: Crear Secciones de Curso
    path('adminage/create-courses/', create_courses_sections_view,
         name='create_courses_sections'),

    # Redirección de finalización
    path('staff-dashboard/', adminage_dashboard_view, name='staff_dashboard'),
    path('', views.loginPage, name='login'),
    path('logout/', views.logoutUser, name='logout'),
    path('student/', views.student_detail, name='student_dashboard'),
    path('class/<int:subject_course_id>/download/',
         views.download_class_list, name='download_class_list'),
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
    # CSV download endpoints
    path('grades/csv/', views.grades_csv, name='grades_csv'),
    path('grades/csv/<int:student_id>/',
         views.grades_csv, name='grades_csv_student'),
    path('class/<int:course_id>/grades/download/',
         views.class_grades_download, name='class_grades_download'),
    path('import/grades/', views.import_grades, name='import_grades'),
    path('import/grades/<int:course_id>/',
         views.import_grades, name='import_grades_class'),

]
