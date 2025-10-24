from urllib import request
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester
from django.http import JsonResponse, HttpResponse
from django.db.models import Q
import unicodedata
from django.contrib.auth.decorators import login_required
import csv
from django.contrib import messages
from .forms import GradeForm, AusenciaForm, AusenciaEditForm
from django.utils import timezone
# Create your views here.


def loginPage(request):
    # If the user is already authenticated, send them to their dashboard
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
        except Exception:
            # If no profile, log out and show login
            logout(request)
            return render(request, "mainapp/login.html")

        if profile.role == 'student' and profile.student:
            return redirect('student_dashboard')
        elif profile.role == 'tutor':
            return redirect('tutor_dashboard')
        elif profile.role == 'professor':
            return redirect('teacher_dashboard')
        else:
            return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        if not username or not password:
            return render(request, "mainapp/login.html", {"error": "Please provide both username and password."})

        try:
            User.objects.get(username=username)
        except User.DoesNotExist:
            return render(request, "mainapp/login.html", {"error": "User does not exist"})

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            profile = request.user.profile
            # Use role string checks consistently
            if profile.role == 'student' and profile.student:
                return redirect('student_dashboard')
            elif profile.role == 'tutor':
                return redirect('tutor_dashboard')
            elif profile.role == 'professor':
                return redirect('teacher_dashboard')
            else:
                return render(request, "forbidden.html", {"user": request.user, "profile": profile})
        else:
            # Authentication failed
            return render(request, "mainapp/login.html", {"error": "Invalid username or password"})

    # Default for GET and other methods: show the login page
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


def sort_key_section(course):

    section = course.Section
    number_part = int(section[0])
    letter_part = section[1]

    return (number_part, letter_part)


@login_required
def teacher_dashboard(request):
    profile = request.user.profile
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})
    else:
        all_students = Students.objects.all().order_by('Name')
        all_grades = Grade.objects.all()
        all_ausencias = Ausencias.objects.all()
        all_courses = Course.objects.all()

    # 2. Sort the list in Python
        sorted_courses = sorted(all_courses, key=sort_key_section)

    # 3. Create separate lists for each 'Tipo'
        eso_courses = []
        bachillerato_courses = []
        ib_courses = []

        for course in sorted_courses:
            if course.Tipo == "Eso":
                eso_courses.append(course)
            elif course.Tipo == "Bachillerato":
                bachillerato_courses.append(course)
            elif course.Tipo == "IB":
                ib_courses.append(course)

        context = {
            "students": all_students,
            "grades": all_grades,
            "ausencias": all_ausencias,
            "courses": all_courses,
            'eso_courses': eso_courses,
            'bachillerato_courses': bachillerato_courses,
            'ib_courses': ib_courses,
        }
        return render(request, "mainapp/teacher_dashboard.html", context)


@login_required
def section_courses(request, section):
    profile = request.user.profile
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # normalize the incoming section
    sec = (section or '').strip().lower()
    mapping = {
        'eso': 'Eso',
        'bachillerato': 'Bachillerato',
        'ib': 'IB',
        'todos': None,
        'all': None,
    }
    target = mapping.get(sec)
    if sec not in mapping:
        # invalid section - return teacher dashboard to be forgiving
        return redirect('teacher_dashboard')

    if target is None:
        courses_qs = Course.objects.all()
    else:
        courses_qs = Course.objects.filter(Tipo=target)

    # reuse the sort helper from above (works even if Section format varies)
    sorted_courses = sorted(list(courses_qs), key=sort_key_section)

    context = {
        'section_label': section.capitalize(),
        'courses': sorted_courses,
        'is_professor': True,
    }
    return render(request, 'mainapp/section_courses.html', context)


@login_required
def class_dashboard(request, course_id):
    profile = request.user.profile
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    course = get_object_or_404(Course, CourseID=course_id)
    subjects_courses = course.subjects_courses_set.all()
    students = Students.objects.filter(
        subjects_courses__course=course).distinct().order_by('Name')

    # Handle absence creation via a right-side panel form
    if request.method == 'POST':
        form = AusenciaForm(request.POST, course=course)
        if form.is_valid():
            # student is a QuerySet/list of Students (multiple selection)
            students_selected = form.cleaned_data.get('students')
            subject = form.cleaned_data.get('subject')
            trimester = form.cleaned_data.get('trimester')
            tipo = form.cleaned_data.get('Tipo')
            date_time = form.cleaned_data.get('date_time')

            created = 0
            for s in students_selected:
                # create Ausencias for each student
                if date_time:
                    a = Ausencias(student=s, subject=subject,
                                  trimester=trimester, Tipo=tipo, date_time=date_time)
                else:
                    a = Ausencias(student=s, subject=subject,
                                  trimester=trimester, Tipo=tipo)
                try:
                    a.save()
                    created += 1
                except Exception:
                    # skip duplicates/validation errors for individual students
                    continue

            if created:
                messages.success(
                    request, f'Ausencias creadas para {created} estudiante(s).')
            else:
                messages.error(
                    request, 'No se creó ninguna ausencia (posibles duplicados).')
            return redirect('class_dashboard', course_id=course.CourseID)
        else:
            messages.error(request, 'Errores en el formulario de ausencia.')
    else:
        form = AusenciaForm(course=course)

    context = {
        "course": course,
        "subjects_courses": subjects_courses,
        "students": students,
        "ausencia_form": form,
    }
    return render(request, "mainapp/class_dashboard.html", context)


@login_required
def student_dashboard_content(request, student_id):
    profile = request.user.profile
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student = get_object_or_404(Students, StudentID=student_id)
    grades = Grade.objects.filter(student=student)
    ausensias = Ausencias.objects.filter(
        student=student).order_by('-date_time')
    # support returning to the class dashboard by passing the course id as a GET param ?course=ID
    return_course = request.GET.get('course')
    context = {
        "student": student,
        "grades": grades,
        "ausencias": ausensias,
        "is_tutor": False,
        "return_course": return_course,
    }
    return render(request, "mainapp/student_dashboard_content.html", context)


@login_required
def tutor_dashboard(request):
    profile = request.user.profile
    if profile.role != 'tutor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

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
    # safely parse selected child index
    try:
        selected_child = int(request.GET.get("child", 0))
    except (TypeError, ValueError):
        selected_child = 0
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
        return render(request, 'forbidden.html', {"user": request.user, "profile": profile})
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(
        ['Estudiante', 'Asignatura', 'Profesor', 'Trimestre', 'Nota', 'Comentario'])
    for grade in grades:
        student = grade.student
        student_name = " ".join(filter(None, [getattr(student, 'Name', ''), getattr(
            student, 'First_Surname', ''), getattr(student, 'Last_Surname', '')])).strip()
        if not student_name:
            student_name = getattr(student, 'Email', 'Unknown')
        subject_name = getattr(grade.subject, 'Name', 'N/A')
        teacher_name = getattr(getattr(grade, 'teacher', None), 'Name', 'N/A')
        trimester_name = getattr(
            getattr(grade, 'trimester', None), 'Name', 'N/A')
        writer.writerow([student_name, subject_name, teacher_name,
                        trimester_name, grade.grade, grade.comments])
    return response


@login_required
def create_edit_grade(request, grade_id=None, student_id=None):
    profile = request.user.profile

    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student_instance = None
    grade_instance = None

    if grade_id:
        grade_instance = get_object_or_404(Grade, id=grade_id)
        student_instance = grade_instance.student
    elif student_id:
        student_instance = get_object_or_404(Students, pk=student_id)

    if request.method == "POST":
        form = GradeForm(request.POST, instance=grade_instance)

        if form.is_valid():
            # 1. Get the model instance without saving to the DB
            g = form.save(commit=False)

        # 2. Assign the student ONLY if it's a new grade
        if not grade_id:
            # 'g' is now defined and we can assign the student
            g.student = student_instance

        # 3. Save the final instance to the database
        g.save()

        messages.success(request, "Grade saved successfully.")
        return redirect('student_dashboard_content', student_id=student_instance.pk)
    else:
        form = GradeForm(instance=grade_instance)

    context = {
        "form": form,
        "is_edit": grade_instance is not None,
        "student": student_instance,
    }
    return render(request, "mainapp/grade_form.html", context)


@login_required
def create_edit_ausencia(request, ausencia_id=None, student_id=None):
    profile = request.user.profile

    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student_instance = None
    ausencia_instance = None

    if ausencia_id:
        ausencia_instance = get_object_or_404(Ausencias, id=ausencia_id)
        student_instance = ausencia_instance.student
    elif student_id:
        student_instance = get_object_or_404(Students, pk=student_id)

    if request.method == "POST":
        form = AusenciaEditForm(request.POST, instance=ausencia_instance)

        if form.is_valid():
            # 1. Get the model instance without saving to the DB
            ausencia = form.save(commit=False)

        # 2. Assign the student ONLY if it's a new ausencia
        if not ausencia_id:
            # 'ausencia' is now defined and we can assign the student
            ausencia.student = student_instance

        # 3. Save the final instance to the database
        ausencia.save()

        messages.success(request, "Absence saved successfully.")
        return redirect('student_dashboard_content', student_id=student_instance.pk)
    else:
        form = AusenciaEditForm(instance=ausencia_instance)

    context = {
        "form": form,
        "is_edit": ausencia_instance is not None,
        "student": student_instance,
    }
    return render(request, "mainapp/ausencia_form.html", context)

# Search view: looks up students by name or email.
# - Only available to users with Profile.role == 'professor'.
# - If a GET parameter 'course' is provided (course ID), the search is limited
#   to students enrolled in that course. This allows searching only within a class.
# - Query parameter 'q' is the search term (case-insensitive, matches Name or Email).


@login_required
def search_students(request):
    profile = request.user.profile
    # Enforce that only professors can use this endpoint
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Get search query and optional course filter from GET params
    query = (request.GET.get('q') or '').strip()
    course_id = request.GET.get('course')  # optional course id to limit search

    students_qs = Students.objects.none()

    # If a course_id is provided and valid, limit students to that course's attendees
    if course_id:
        try:
            course_obj = Course.objects.get(CourseID=course_id)
            # students related through Subjects_Courses -> students M2M
            students_qs = Students.objects.filter(
                subjects_courses__course=course_obj).distinct()
        except Course.DoesNotExist:
            # If course doesn't exist, keep empty queryset (no results)
            students_qs = Students.objects.none()
    else:
        # No course filter: search across all students
        students_qs = Students.objects.all()

    # If a query term exists, filter by Name or Email in a case-insensitive,
    # accent-insensitive way.
    # Strategy:
    # 1) Normalize the user query by removing diacritics and lowercasing.
    # 2) Try to use the DB-side `unaccent` function (Postgres) via Func to
    #    annotate unaccented fields and filter using icontains against the
    #    normalized query. This is efficient for large datasets.
    # 3) If the DB-side approach fails (no unaccent support or DB error), fall
    #    back to evaluating the queryset in Python and comparing normalized
    #    strings (less efficient but robust).
    def _strip_accents(text):
        if not text:
            return ''
        nkfd = unicodedata.normalize('NFKD', text)
        return ''.join([c for c in nkfd if not unicodedata.combining(c)])

    if query:
        # normalized query (no accents, lowercase)
        qnorm = _strip_accents(query).lower()

        # Attempt DB-side unaccent (Postgres unaccent extension) but detect at
        # request time whether the DB supports it. We build an annotated
        # queryset and try to evaluate a single row to force any SQL errors
        # (e.g. missing unaccent function). If an error occurs, fall back to
        # Python-side normalization/filtering.
        try:
            from django.db.models import Func, F

            students_qs_candidate = students_qs.annotate(
                name_unaccent=Func(F('Name'), function='unaccent'),
                email_unaccent=Func(F('Email'), function='unaccent'),
            ).filter(
                Q(name_unaccent__icontains=qnorm) | Q(
                    email_unaccent__icontains=qnorm)
            ).order_by('Name')

            # Force evaluation of a single element to detect DB-side errors
            try:
                _ = list(students_qs_candidate[:1])
                students_qs = students_qs_candidate
            except Exception:
                # If the DB query fails (e.g. unaccent missing), raise to trigger fallback
                raise
        except Exception:
            matched = []
            for s in students_qs:
                name_val = getattr(s, 'Name', '') or ''
                email_val = getattr(s, 'Email', '') or ''
                if qnorm in _strip_accents(name_val).lower() or qnorm in _strip_accents(email_val).lower():
                    matched.append(s)
            students_qs = matched
    else:
        # If no query and no course filter, return empty queryset to avoid dumping all students
        if not course_id:
            students_qs = Students.objects.none()

    # Build results: each item contains the student and a list of their course labels
    results = []
    for s in students_qs:
        # Fetch courses related to this student via Subjects_Courses -> course
        courses = Course.objects.filter(
            subjects_courses__students=s).distinct()
        # Format each course as "Tipo Section" e.g. "Eso 1B"
        course_labels = [f"{c.Tipo} {c.Section}" for c in courses]
        results.append({
            'student': s,
            'courses': course_labels,
        })

    context = {
        'query': query,
        'results': results,
        'course_id': course_id,
    }
    return render(request, 'mainapp/search_results.html', context)
