from django.urls import path
from . import views
urlpatterns = [
    path("", views.index),
    path('student/<int:student_name>/',
         views.student_detail, name='student_detail'),
]
