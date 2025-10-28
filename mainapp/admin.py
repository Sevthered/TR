from django.contrib import admin
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester, Subjects_Courses, School_year, Students_Courses

admin.site.register(Students)
admin.site.register(Profile)
admin.site.register(Course)
admin.site.register(Teachers)
admin.site.register(Grade)
admin.site.register(Ausencias)
admin.site.register(Subjects_Courses)
admin.site.register(Subjects)
admin.site.register(Trimester)
admin.site.register(School_year)
admin.site.register(Students_Courses)
