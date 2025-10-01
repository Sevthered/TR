from django.shortcuts import render
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
# Create your views here.


@login_required
def student_detail(request):
    profile = request.user.profile
    if profile.role != 'student' or not profile.student:
        return render(request, "mainapp/forbidden.html")

    student = Profile.student
    grades = Grade.objects.filter(student=student)
    aucensias = Ausencias.objects.filter(student=student)
    context = {
        "student": student,
        "grades": grades,
        "ausencias": aucensias,
    }
    return render(request, "mainapp/student_file.html", context)


@login_required
def teacher_dashboard(request):

    profile = request.user.profile
    if profile.role != 'professor' or not profile.student:
        return render(request, "mainapp/forbidden.html")

    all_students = Students.objects.all()
    all_grades = Grade.objects.all()
    all_ausencias = Ausencias.objects.all()
