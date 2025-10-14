from django.contrib import admin
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester

admin.site.register(Students)
admin.site.register(Profile)
admin.site.register(Course)
admin.site.register(Teachers)
admin.site.register(Grade)
admin.site.register(Ausencias)
admin.site.register(Subjects)
admin.site.register(Trimester)
