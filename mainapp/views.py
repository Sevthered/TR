from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
import csv
from django.contrib import messages
from .forms import GradeForm
# Create your views here.


def loginPage(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        try:
            User.objects.get(username=username)
        except:
            return render(request, "mainapp/login.html", {"error": "User does not exist"})

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            profile = request.user.profile
            if profile.role == 'student' and profile.student:
                return redirect('student_dashboard')
            elif profile.role == 'tutor' and profile.role:
                return redirect('tutor_dashboard')
            elif profile.role == 'professor' and profile.role:
                return teacher_dashboard(request)
            else:
                return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    return render(request, "mainapp/login.html")


def logoutUser(request):
    logout(request)
    return redirect('login')


@login_required
def student_detail(request):
    profile = request.user.profile
    if profile.role != 'student' or not profile.student:
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

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
    if profile.role != 'professor' or not profile.role:
        return render(request, "forbidden.html")
    else:
        all_students = Students.objects.all().order_by('Name')
        all_grades = Grade.objects.all()
        all_ausencias = Ausencias.objects.all()

        context = {
            "students": all_students,
            "grades": all_grades,
            "ausencias": all_ausencias,
        }
        return render(request, "mainapp/teacher_dashboard.html", context)


@login_required
def student_dashboard_content(request, student_id):
    profile = request.user.profile
    if profile.role != 'professor' or not profile.role:
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student = get_object_or_404(Students, StudentID=student_id)
    grades = Grade.objects.filter(student=student)
    ausensias = Ausencias.objects.filter(
        student=student).order_by('-date_time')
    context = {
        "student": student,
        "grades": grades,
        "ausencias": ausensias,
        "is_tutor": False,
    }
    return render(request, "mainapp/student_dashboard_content.html", context)


@login_required
def tutor_dashboard(request):
    profile = request.user.profile
    if profile.role != 'tutor' or not profile.role:
        return render(
            request,
            "forbidden.html",
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


@login_required
def grades_csv(request):
    profile = request.user.profile
    grades = Grade.objects.none()
    filename = "student_data.csv"

    if profile.role == "student" and profile.student:
        # Student: only their grades
        student = profile.student
        grades = Grade.objects.filter(student=student)
        filename = f"{student.Name}_{student.First_Surname}_grades.csv"
    elif profile.role == "tutor":
        # Tutor: all their children's grades, grouped by child
        children = list(profile.children.all())
        grades = []
        for child in children:
            child_grades = Grade.objects.filter(student=child)
            grades.extend(child_grades)
        filename = f"{request.user.username}_children_grades.csv"
    elif profile.role == "professor":
        # Professor: all grades
        grades = Grade.objects.all()
        filename = "all_grades.csv"
    else:
        return render('forbidden.html', {"user": request.user, "profile": profile})
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(
        ['Estudiante', 'Asignatura', 'Profesor', 'Trimestre', 'Nota', 'Comentario'])
    for grade in grades:
        writer.writerow(
            [f"{grade.student.Name} {grade.student.First_Surname} {grade.student.Last_Surname}",
             grade.subject.Name,
             grade.teacher.Name if grade.teacher else "N/A",
             grade.trimester.Name if grade.trimester else "N/A",
             grade.grade,
             grade.comments])
    return response


@login_required
def create_edit_grade(request, grade_id=None):
    profile = request.user.profile
    if profile.role != 'professor' or not profile.professor:
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})
    else:
        if grade_id:
            grade_instance = get_object_or_404(Grade, id=grade_id)
        else:
            grade_instance = None

    if request.method == "POST":
        form = GradeForm(request.POST, instance=grade_instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Grade saved successfully.")
            return redirect('student_dashboard')
    else:
        form = GradeForm(instance=grade_instance)

    context = {
        "form": form,
        "is_edit": grade_instance is not None,
    }
    return render(request, "mainapp/grade_form.html", context)
