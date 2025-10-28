from django.http import JsonResponse
import profile
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.shortcuts import render, get_object_or_404, redirect
from .models import Students, Profile, Course, Students_Courses, Teachers, Subjects, Grade, Ausencias, Trimester, Subjects_Courses, School_year
from django.http import JsonResponse, HttpResponse
from django.db.models import Q
import unicodedata
from django.contrib.auth.decorators import login_required
import csv
from django.contrib import messages
from .forms import GradeForm, AusenciaForm, AusenciaEditForm, SchoolYearForm, CourseCreationForm, CourseSectionForm, MAIN_COURSES, SubjectAssignmentForm, StudentCreationForm
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.forms import formset_factory
from django.http import Http404
from django.urls import reverse
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
        elif profile.role == 'administrator':
            return redirect('adminage_dashboard')
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
            school_year = form.cleaned_data.get('school_year')
            trimester = form.cleaned_data.get('trimester')
            tipo = form.cleaned_data.get('Tipo')
            date_time = form.cleaned_data.get('date_time')

            created = 0
            for s in students_selected:
                # create Ausencias for each student
                if date_time:
                    a = Ausencias(student=s, subject=subject,
                                  trimester=trimester, Tipo=tipo, date_time=date_time, school_year=school_year)
                else:
                    a = Ausencias(student=s, subject=subject,
                                  trimester=trimester, Tipo=tipo, school_year=school_year)
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
def download_class_list(request, subject_course_id):
    # 1. Authorization Check (Still important)
    if request.user.profile.role != 'professor':
        return HttpResponse("Acceso denegado.", status=403)

    # The ID received from the template link is the Course ID.
    course_id_received = subject_course_id

    # 2. Find the Subjects_Courses instance using the Course ID.
    # We use .filter() and .first() to prevent the MultipleObjectsReturned error
    # if more than one Subjects_Courses exists for this Course ID.
    # NOTE: This assumes you only need one class's student list per Course ID.
    subject_course = Subjects_Courses.objects.filter(
        course__CourseID=course_id_received
    ).first()

    if not subject_course:
        return HttpResponse("No se encontró una clase asociada a este curso.", status=404)

    # 3. Create the CSV response header
    response = HttpResponse(content_type='text/csv')
    filename = f"{subject_course.course.Tipo}{subject_course.course.Section}_import_template.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # 4. Create CSV writer and write data
    writer = csv.writer(response)

    # Write the import format header
    writer.writerow(['Nombre_Estudiante', 'Asignatura', 'Trimestre',
                    'Año_Escolar', 'Nota', 'Tipo_Nota', 'Numero_Tipo_Nota', 'Comentarios'])

    # Get the student list directly from the found Subjects_Courses object
    students = subject_course.students.all().order_by('Name')

    # Get available subjects for this course
    subjects = Subjects.objects.filter(
        subjects_courses__course=subject_course.course
    ).distinct().order_by('Name')

    # Get current school year (or create a default one)
    current_year = timezone.now().year
    school_year_str = f"{current_year}-{current_year + 1}"

    # Create template rows - one row per student
    for student in students:
        writer.writerow([
            student.Name,  # Nombre_Estudiante
            '',  # Asignatura (empty for template)
            '',  # Trimestre (empty for template)
            school_year_str,  # Año_Escolar
            '',  # Nota (empty for template)
            'examen',  # Tipo_Nota (default)
            '0',  # Numero_Tipo_Nota (default)
            ''  # Comentarios (empty for template)
        ])

    return response


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
def grades_csv(request, student_id=None):
    profile = request.user.profile
    grades = Grade.objects.none()
    filename = "student_data.csv"

    if profile.role == "student" and profile.student:
        # Student: only their grades
        student = profile.student
        grades = Grade.objects.filter(student=student)
        filename = f"{student.Name}_notas.csv"
    elif profile.role == "tutor":
        # Tutor: all their children's grades
        children = list(profile.children.all())
        grades = Grade.objects.filter(student__in=children)
        filename = f"{request.user.username}_notas.csv"
    elif profile.role == "professor":
        if student_id:
            # Professor: specific student's grades
            student = get_object_or_404(Students, pk=student_id)
            grades = Grade.objects.filter(student=student)
            filename = f"{student.Name}_notas.csv"
        else:
            # Professor: all grades
            grades = Grade.objects.all()
            filename = "all_grades.csv"
    else:
        return render(request, 'forbidden.html', {"user": request.user, "profile": profile})

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(
        ['Estudiante', 'Asignatura', 'Trimestre', 'Año Escolar', 'Nota', 'Tipo de Nota', 'Numero tipo de Nota', 'Comentario'])

    for grade in grades:
        student = grade.student
        student_name = student.Name
        subject_name = grade.subject.Name
        trimester_name = grade.trimester.Name
        school_year = grade.school_year.year
        writer.writerow([
            student_name,
            subject_name,
            trimester_name,
            school_year,
            grade.grade,
            grade.grade_type,
            grade.grade_type_number,
            grade.comments
        ])
    return response


@login_required
def class_grades_download(request, course_id):
    profile = request.user.profile
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    course = get_object_or_404(Course, CourseID=course_id)

    # Get all students in this course
    students_in_course = Students.objects.filter(
        subjects_courses__course=course).distinct()

    # Get all subjects that have grades for students in this course
    subjects_in_course = Subjects.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('Name')

    # Get all trimesters and school years from grades of students in this course
    trimesters = Trimester.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('Name')
    school_years = School_year.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('year')

    # Get all grade types from grades of students in this course
    grade_types = Grade.objects.filter(
        student__in=students_in_course).values_list('grade_type', flat=True).distinct().order_by('grade_type')

    if request.method == 'POST':
        # Process the filter form
        selected_subject_id = request.POST.get('subject')
        selected_trimester_id = request.POST.get('trimester')
        selected_school_year_id = request.POST.get('school_year')
        selected_grade_type = request.POST.get('grade_type')

        # Start with grades from students in this course
        grades = Grade.objects.filter(student__in=students_in_course)

        # Apply filters
        if selected_subject_id:
            grades = grades.filter(subject_id=selected_subject_id)
        if selected_trimester_id:
            grades = grades.filter(trimester_id=selected_trimester_id)
        if selected_school_year_id:
            grades = grades.filter(school_year_id=selected_school_year_id)
        if selected_grade_type:
            grades = grades.filter(grade_type=selected_grade_type)

        # Generate filename based on filters
        filename_parts = [course.Tipo, course.Section]
        if selected_subject_id:
            subject = get_object_or_404(Subjects, pk=selected_subject_id)
            filename_parts.append(subject.Name)
        if selected_trimester_id:
            trimester = get_object_or_404(Trimester, pk=selected_trimester_id)
            filename_parts.append(f"Trimestre_{trimester.Name}")
        if selected_school_year_id:
            school_year = get_object_or_404(
                School_year, pk=selected_school_year_id)
            filename_parts.append(school_year.year)
        if selected_grade_type:
            filename_parts.append(f"Tipo_{selected_grade_type}")

        filename = "_".join(filename_parts) + "_grades.csv"

        # Generate CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(
            ['Estudiante', 'Asignatura', 'Trimestre', 'Año Escolar', 'Nota', 'Tipo de Nota', 'Comentario'])

        for grade in grades:
            student = grade.student
            student_name = student.Name
            subject_name = grade.subject.Name
            trimester_name = grade.trimester.Name
            school_year = grade.school_year.year
            writer.writerow([
                student_name,
                subject_name,
                trimester_name,
                school_year,
                grade.grade,
                grade.grade_type,
                grade.comments
            ])
        return response

    # GET request - show the filter form
    context = {
        "course": course,
        "subjects": subjects_in_course,
        "trimesters": trimesters,
        "school_years": school_years,
        "grade_types": grade_types,
    }
    return render(request, "mainapp/class_grades_download.html", context)


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


@login_required
def import_grades(request, course_id=None):
    profile = request.user.profile
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    course = None
    if course_id:
        course = get_object_or_404(Course, CourseID=course_id)

    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            messages.error(request, 'Por favor selecciona un archivo CSV.')
            return render(request, 'mainapp/import_grades.html', {'course': course})

        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'El archivo debe ser un CSV.')
            return render(request, 'mainapp/import_grades.html', {'course': course})

        created_count = 0
        updated_count = 0
        error_count = 0
        errors = []

        try:
            # Read CSV content
            csv_content = csv_file.read().decode('utf-8')
            reader = csv.DictReader(csv_content.splitlines())

            for row_num, row in enumerate(reader, start=2):
                try:
                    # Parse CSV row - support both Spanish and English column names
                    student_name = row.get('Nombre_Estudiante') or row.get(
                        'student_name', '').strip()
                    subject_name = row.get('Asignatura') or row.get(
                        'subject_name', '').strip()
                    trimester_name = row.get('Trimestre') or row.get(
                        'trimester_name', '').strip()
                    school_year_str = row.get('Año_Escolar') or row.get(
                        'school_year', '').strip()
                    grade_value = float(row.get('Nota') or row.get('grade', 0))
                    grade_type = (row.get('Tipo_Nota') or row.get(
                        'grade_type', 'examen')).strip()
                    grade_type_number = int(
                        row.get('Numero_Tipo_Nota') or row.get('grade_type_number', 0) or 0)
                    comments = (row.get('Comentarios')
                                or row.get('comments', '')).strip()

                    # Get or create objects
                    student = Students.objects.get(Name=student_name)
                    subject = Subjects.objects.get(Name=subject_name)

                    # Get or create school year
                    school_year, _ = School_year.objects.get_or_create(
                        year=school_year_str,
                        defaults={'year': school_year_str}
                    )

                    # Get or create trimester
                    trimester, _ = Trimester.objects.get_or_create(
                        Name=int(trimester_name),
                        school_year=school_year,
                        defaults={'Name': int(
                            trimester_name), 'school_year': school_year}
                    )

                    # Create or update grade
                    grade, created = Grade.objects.update_or_create(
                        student=student,
                        subject=subject,
                        trimester=trimester,
                        school_year=school_year,
                        grade_type=grade_type,
                        grade_type_number=grade_type_number,
                        defaults={
                            'grade': grade_value,
                            'comments': comments,
                        }
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                except Students.DoesNotExist:
                    errors.append(
                        f"Fila {row_num}: Estudiante '{student_name}' no encontrado")
                    error_count += 1
                except Subjects.DoesNotExist:
                    errors.append(
                        f"Fila {row_num}: Asignatura '{subject_name}' no encontrada")
                    error_count += 1
                except Exception as e:
                    errors.append(f"Fila {row_num}: {str(e)}")
                    error_count += 1

            # Show results
            if created_count > 0:
                messages.success(request, f'✓ Creadas: {created_count} notas')
            if updated_count > 0:
                messages.info(
                    request, f'↻ Actualizadas: {updated_count} notas')
            if error_count > 0:
                messages.error(request, f'❌ Errores: {error_count} notas')
                for error in errors[:10]:  # Show first 10 errors
                    messages.error(request, error)
                if len(errors) > 10:
                    messages.error(
                        request, f'... y {len(errors) - 10} errores más')

        except Exception as e:
            messages.error(request, f'Error al procesar el archivo: {str(e)}')

    context = {
        'course': course,
    }
    return render(request, 'mainapp/import_grades.html', context)


@login_required
def adminage_dashboard_view(request):
    profile = request.user.profile
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})
    """
    Dashboard principal con botones para iniciar los procesos de configuración.
    """
    context = {
        'title': 'Panel de Administración Escolar',
        # Obtener la lista de años escolares para la plantilla
        'school_years': School_year.objects.all().order_by('-year')
    }
    return render(request, "adminage/adminage_dashboard.html", context)


# =======================================================
# --- VISTA 1: CREAR AÑO ESCOLAR ---
# =======================================================
@login_required
def create_school_year_view(request):
    profile = request.user.profile
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})
    if request.method == 'POST':
        form = SchoolYearForm(request.POST)
        if form.is_valid():
            # 1. Crear el School_year
            school_year_obj = form.save()

            # 2. Crear automáticamente los 3 Trimestres asociados
            trimestres_a_crear = [
                Trimester(Name=1, school_year=school_year_obj),
                Trimester(Name=2, school_year=school_year_obj),
                Trimester(Name=3, school_year=school_year_obj),
            ]
            Trimester.objects.bulk_create(trimestres_a_crear)

            messages.success(
                request, f"Año Escolar {school_year_obj.year} creado. Se han generado 3 trimestres asociados.")

            # 3. Redirige a la Vista 2 (creación de cursos)
            url = reverse('create_courses_sections')
            return redirect(f'{url}?school_year_id={school_year_obj.pk}')
        else:
            messages.error(
                request, "Error al crear el Año Escolar. Por favor, corrija los errores.")
    else:
        form = SchoolYearForm()

    context = {
        'form': form,
        'title': 'Crear Nuevo Año Escolar'
    }
    return render(request, "adminage/create_school_year.html", context)


# =======================================================
# --- VISTA 2: CREAR SECCIONES DE CURSO (Multi-Step) ---
# =======================================================
@login_required
def create_courses_sections_view(request):
    profile = request.user.profile
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    school_year_id = request.GET.get('school_year_id')

    if not school_year_id:
        messages.error(request, "Debe comenzar definiendo un Año Escolar.")
        # Redirige al botón de inicio
        return redirect('adminage_dashboard')

    try:
        school_year = School_year.objects.get(pk=school_year_id)
    except School_year.DoesNotExist:
        raise Http404("Año Escolar no encontrado.")

    context = {'school_year': school_year}

    # --- PASO 1 POST (Selección del Tipo) ---
    if request.method == 'POST' and request.POST.get('step') == 'select_type':

        form_main = CourseCreationForm(
            request.POST,
            initial_school_year_id=school_year_id,
            course_type_initial=request.POST.get('course_tipo')
        )

        if not form_main.is_valid():
            messages.error(
                request, "Error de validación. Revise la selección inicial.")
            context['form'] = form_main
            return render(request, "adminage/create_courses_step1.html", context)

        course_tipo = form_main.cleaned_data['course_tipo']

        return _render_step2(request, course_tipo, school_year, form_main)

    # --- PASO 2 POST (Guardado de Secciones) ---
    elif request.method == 'POST' and request.POST.get('step') == 'confirm_sections':

        course_tipo = request.POST.get('course_tipo')

        form_main = CourseCreationForm(
            request.POST,
            initial_school_year_id=school_year_id,
            course_type_initial=course_tipo
        )

        if not form_main.is_valid():
            messages.error(request, "Error de validación. Vuelva a empezar.")
            return redirect('adminage_dashboard')  # Redirige al inicio

        CourseFormSet = formset_factory(CourseSectionForm, extra=0)
        formset = CourseFormSet(request.POST)

        if formset.is_valid():
            num_created = 0

            for form_section in formset:
                if form_section.cleaned_data:
                    main_course_name = form_section.cleaned_data['main_course_name']
                    num_subsections = form_section.cleaned_data['num_subsections']
                    subsection_letters = [chr(65 + i)
                                          for i in range(num_subsections)]

                    new_courses = []
                    for letter in subsection_letters:
                        new_courses.append(
                            Course(
                                Tipo=course_tipo,
                                Section=f"{main_course_name}{letter}",
                                school_year=school_year
                            )
                        )
                    Course.objects.bulk_create(new_courses)
                    num_created += len(new_courses)

            messages.success(
                request, f"{num_created} secciones de cursos ({course_tipo}) creadas exitosamente para {school_year}.")

            # Redirige de vuelta al dashboard
            return redirect('adminage_dashboard')

        else:
            messages.error(
                request, "Por favor, corrige los errores en las secciones.")
            return _render_step2(request, course_tipo, school_year, form_main, formset=formset)

    # --- PASO 1 GET (Carga Inicial) ---
    else:
        form = CourseCreationForm(
            initial_school_year_id=school_year_id,
            initial={'school_year': school_year}
        )
        context['form'] = form
        return render(request, "adminage/create_courses_step1.html", context)


# Función auxiliar para renderizar el Paso 2
def _render_step2(request, course_tipo, school_year, form_main, formset=None):
    profile = request.user.profile
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    if not formset:
        CourseFormSet = formset_factory(CourseSectionForm, extra=0)
        initial_data = []
        if course_tipo in MAIN_COURSES:
            for main_course_num in MAIN_COURSES[course_tipo]:
                initial_data.append({
                    'main_course_name': str(main_course_num),
                    'display_name': f"{main_course_num}º {course_tipo}"
                })
        formset = CourseFormSet(initial=initial_data)

    context = {
        'form_main': form_main,
        'formset': formset,
        'course_tipo': course_tipo,
        'school_year': school_year,
        'title': f"Definir Secciones para {course_tipo} ({school_year})"
    }
    return render(request, "adminage/create_courses_step2.html", context)


@login_required
def assign_subjects_view(request):
    """
    Permite asignar una asignatura y un profesor a múltiples trimestres, 
    seleccionando qué estudiantes del curso serán asignados a esa materia.
    """

    course_types = Course.COURSE_TYPE_CHOICES

    # 1. DEFINICIÓN E INICIALIZACIÓN DE VARIABLES
    latest_school_year = School_year.objects.order_by(
        '-year').only('pk').first()
    school_year_id = request.GET.get('school_year_id') or (
        str(latest_school_year.pk) if latest_school_year else '')

    current_form = SubjectAssignmentForm()
    selected_course_id = request.GET.get('course_id')
    current_school_year = None
    trimesters = []
    course_students_links = None
    target_course = None

    if school_year_id:
        try:
            current_school_year = School_year.objects.get(pk=school_year_id)
            trimesters = Trimester.objects.filter(
                school_year=current_school_year).order_by('Name')

            current_form.fields['subject'].queryset = Subjects.objects.all().order_by(
                'Name')
            current_form.fields['teacher'].queryset = Teachers.objects.all().order_by(
                'Name')

        except School_year.DoesNotExist:
            messages.error(request, "Año escolar no válido.")
            return redirect('assign_subjects')

    # Obtener estudiantes del curso seleccionado (para el GET y re-renderizado)
    if selected_course_id:
        try:
            target_course = Course.objects.get(pk=selected_course_id)
            # Obtenemos los REGISTROS DE RELACIÓN (Students_Courses)
            course_students_links = Students_Courses.objects.filter(
                course_section=target_course
            ).select_related('student').order_by('student__Name')
        except Course.DoesNotExist:
            messages.warning(
                request, "El ID de curso seleccionado no es válido.")
            selected_course_id = None

    # 2. Manejo del POST (Asignación)
    if request.method == 'POST':

        # Recuperamos las variables del POST (campos ocultos)
        selected_course_id = request.POST.get('course_id')
        school_year_id_post = request.POST.get('school_year_id')
        final_school_year_id = school_year_id_post or school_year_id

        # Validaciones iniciales
        if not selected_course_id or not final_school_year_id:
            messages.error(
                request, "Error: Debe seleccionar una sección de curso y un año escolar válido.")
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}')

        try:
            target_course = Course.objects.get(pk=selected_course_id)
        except Course.DoesNotExist:
            messages.error(request, "Sección de curso no válida.")
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}')

        # Recargamos el formulario con los datos POST
        form = SubjectAssignmentForm(request.POST)
        form.fields['subject'].queryset = Subjects.objects.all().order_by(
            'Name')
        form.fields['teacher'].queryset = Teachers.objects.all().order_by(
            'Name')

        if form.is_valid():

            # --- 🟢 LÓGICA DE TRIMESTRES ---
            trimester_ids_selected = request.POST.getlist(
                'trimesters_selected')

            if not trimester_ids_selected:
                messages.error(
                    request, "Error: Debe seleccionar al menos un trimestre.")
                current_form = form
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            try:
                # Obtenemos solo los objetos Trimester seleccionados y que pertenecen al año
                selected_trimesters = Trimester.objects.filter(
                    pk__in=trimester_ids_selected,
                    school_year__pk=final_school_year_id
                )
            except ValueError:
                messages.error(
                    request, "Error de datos: IDs de trimestre no válidos.")
                current_form = form
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            # --- 🟢 LÓGICA DE ESTUDIANTES ---
            assigned_students_courses_ids_selected = request.POST.getlist(
                'student_links_selected')

            try:
                # Convertimos los IDs seleccionados (que son registros de Students_Courses) a enteros
                assigned_students_courses_ids = [
                    int(pk) for pk in assigned_students_courses_ids_selected if pk]
            except ValueError:
                messages.error(
                    request, "Error de datos: Los IDs de estudiante seleccionados no son válidos.")
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            student_count = len(assigned_students_courses_ids)

            subject = form.cleaned_data['subject']
            teacher = form.cleaned_data['teacher']
            newly_created_objects = []

            # 1. Crear/Obtener la asignación Subjects_Courses para cada TRIMESTRE SELECCIONADO
            for trimester in selected_trimesters:
                assignment, created = Subjects_Courses.objects.get_or_create(
                    subject=subject,
                    course=target_course,
                    trimester=trimester,
                    defaults={'teacher': teacher}
                )
                # Actualizamos profesor si ya existía
                if not created and assignment.teacher != teacher:
                    assignment.teacher = teacher
                    assignment.save()

                newly_created_objects.append(assignment)

            # 2. Asignar los REGISTROS DE RELACIÓN (Estudiantes seleccionados) a CADA objeto Subjects_Courses
            if student_count > 0:
                for assignment in newly_created_objects:
                    # Usamos el nombre del campo ajustado, ej. 'assigned_course_sections'
                    assignment.assigned_course_sections.set(
                        assigned_students_courses_ids)

                messages.success(
                    request, f"Asignación de {subject.Name} creada/actualizada para {len(newly_created_objects)} trimestre(s) y {student_count} estudiantes.")

            else:
                # Si no hay estudiantes seleccionados, limpiamos el campo ManyToMany
                for assignment in newly_created_objects:
                    assignment.assigned_course_sections.clear()
                messages.warning(
                    request, f"Asignación de {subject.Name} creada para {len(newly_created_objects)} trimestre(s), pero no se seleccionó ningún estudiante.")

            # Redirigir al GET para limpiar el POST y mantener los filtros
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

        else:
            # Si el formulario falla, re-renderizamos con el error
            messages.error(
                request, "Error en el formulario de asignación. Revise Asignatura y Profesor.")
            current_form = form
            # Nota: Recargar la lista de estudiantes si el POST falló
            if selected_course_id and target_course:
                course_students_links = Students_Courses.objects.filter(
                    course_section=target_course
                ).select_related('student').order_by('student__Name')

    # 3. Manejo del GET y Contexto Final
    context = {
        'title': 'Asignar Asignaturas a Clases',
        'form': current_form,
        'course_types': course_types,
        'school_year_id': school_year_id,
        'selected_course_id': selected_course_id,
        'current_school_year': current_school_year,
        'trimesters': trimesters,
        'course_students_links': course_students_links,
    }
    return render(request, "adminage/assign_subjects.html", context)
# =======================================================
# B. Endpoint AJAX para Carga Dinámica de Secciones
# =======================================================


@login_required
def load_course_sections(request):
    """
    Endpoint AJAX para devolver los niveles o las secciones en cascada.
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    level = request.GET.get('level')

    if not school_year_id or not course_type:
        return JsonResponse({'data': []})

    try:
        sections_query = Course.objects.filter(
            school_year__pk=school_year_id,
            Tipo=course_type,
        ).order_by('Section')

        # --- MODO 1: Devolver NIVELES (al seleccionar Tipo) ---
        if not level:
            all_sections_names = sections_query.values_list(
                'Section', flat=True).distinct()

            main_levels = MAIN_COURSES.get(course_type, [])
            available_levels = []

            for lvl in main_levels:
                lvl_str = str(lvl)
                if any(s.startswith(lvl_str) for s in all_sections_names):
                    available_levels.append({
                        'value': lvl_str,
                        'text': f'{lvl_str}º {course_type}'
                    })

            return JsonResponse({'mode': 'LEVELS', 'data': available_levels})

        # --- MODO 2: Devolver SECCIONES FINALES (al seleccionar Nivel) ---
        else:
            final_sections = sections_query.filter(
                Section__startswith=level
            ).values('CourseID', 'Section')

            sections_data = []
            for s in final_sections:
                sections_data.append({
                    'id': s['CourseID'],
                    'text': s['Section'],
                })

            return JsonResponse({'mode': 'SECTIONS', 'data': sections_data})

    except Exception as e:
        print(f"!!! ERROR FATAL EN load_course_sections (AJAX) !!!: {e}")
        return JsonResponse({'error': f'Error interno del servidor: {str(e)}'}, status=500)


# =======================================================
# --- VISTA ÚNICA: CREAR ESTUDIANTE Y ASIGNAR CLASE ---
# =======================================================
@login_required
def create_and_assign_student_view(request):
    """
    Crea un nuevo estudiante y luego crea el registro de relación
    en Students_Courses para asignarlo a una sección de Course.
    """

    course_types = Course.COURSE_TYPE_CHOICES

    latest_school_year = School_year.objects.order_by(
        '-year').only('pk').first()
    latest_school_year_id = str(
        latest_school_year.pk) if latest_school_year else ''

    current_form = StudentCreationForm()

    if request.method == 'POST':
        current_form = StudentCreationForm(request.POST)
        course_id = request.POST.get('course_id')

        if current_form.is_valid():

            if not course_id:
                messages.error(
                    request, "Error: Debe seleccionar una sección de curso (Tipo, Nivel, Sección) para asignar al estudiante.")
            else:
                try:
                    target_course = Course.objects.get(pk=course_id)
                except Course.DoesNotExist:
                    messages.error(
                        request, "Error: La sección de curso seleccionada no es válida.")
                else:
                    # 1. CREAR ESTUDIANTE (Tabla Students)
                    new_student = current_form.save()

                    # 2. CREAR LA RELACIÓN (Tabla Students_Courses)
                    # ✅ LÓGICA CLAVE: Usa el objeto Course y el estudiante recién creado.
                    Students_Courses.objects.create(
                        student=new_student,
                        course_section=target_course
                    )

                    messages.success(
                        request, f"Estudiante '{new_student.Name}' creado y asignado a {target_course.Section} exitosamente.")

                    return redirect('create_and_assign_student')

        else:
            messages.error(
                request, "Error en los datos del estudiante. Revise el Nombre y Email.")

    context = {
        'title': 'Crear Estudiante y Asignar Clase',
        'form': current_form,
        'course_types': course_types,
        'current_school_year_id': latest_school_year_id,
        'selected_course_id': request.POST.get('course_id', ''),
    }
    return render(request, "adminage/create_and_assign_student.html", context)
