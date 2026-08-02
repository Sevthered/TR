from django.contrib import admin
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester, Subjects_Courses, School_year, Students_Courses

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """`role` grants privilege, so only a superuser may change it.

    With the default ModelAdmin any staff account holding change_profile
    could set its own role to administrator.
    """

    list_display = ('user', 'role', 'student', 'teacher')
    list_filter = ('role',)

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ()
        return ('role',)


admin.site.register(Students)
admin.site.register(Course)
admin.site.register(Teachers)
admin.site.register(Grade)
admin.site.register(Ausencias)
admin.site.register(Subjects_Courses)
admin.site.register(Subjects)
admin.site.register(Trimester)
admin.site.register(School_year)
admin.site.register(Students_Courses)
