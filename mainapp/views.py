from django.shortcuts import render, get_object_or_404
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
# Create your views here.


@login_required
def student_detail(request):
    profile = request.user.profile
    if profile.role != 'student' or not profile.student:
        return render(request, "mainapp/forbidden.html")

    student = profile.student
    grades = Grade.objects.filter(student=student)
    ausensias = Ausencias.objects.filter(
        student=student).order_by('-date_time')
    context = {
        "student": student,
        "grades": grades,
        "ausencias": ausensias,
        "is_tutor": False,
    }
    return render(request, "mainapp/student_file.html", context)


@login_required
def teacher_dashboard(request):
    profile = request.user.profile
    if profile.role != 'professor' or not profile.professor:
        return render(request, "mainapp/forbidden.html")

    all_students = Students.objects.all()
    all_grades = Grade.objects.all()
    all_ausencias = Ausencias.objects.all()


@login_required
def tutor_dashboard(request):
    profile = request.user.profile
    if profile.role != 'tutor' or not profile.role:
        return render(
            request,
            "mainapp/forbidden.html",
            {"user": request.user, "profile": profile}
        )

    children = profile.children.all()
    children_info = []
    for child in children:
        grades = Grade.objects.filter(student=child)
        ausensias = Ausencias.objects.filter(
            student=child).order_by('-date_time')
        children_info.append({
            "student": child,
            "grades": grades,
            "ausensias": ausensias,
        })
    selected_child = int(request.GET.get("child", 0))
    selected_child_obj = children_info[selected_child] if children_info and 0 <= selected_child < len(
        children_info) else None
    context = {
        "children_info": children_info,
        "selected_child": selected_child,
        "selected_child_obj": selected_child_obj,
        "is_tutor": True,
    }
    return render(request, "mainapp/student_file.html", context)
