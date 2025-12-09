from .models import Students, Grade, Ausencias, School_year, Trimester, Profile
from django.shortcuts import render, get_object_or_404
from .models import (
    School_year, Course, Students, Students_Courses
)
from django.shortcuts import render, redirect
from django.http import JsonResponse
import profile
from django.db import transaction
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
from django.forms import formset_factory, ModelChoiceField
from django.http import Http404
from django.urls import reverse
# Create your views here.


def loginPage(request):
    # Check authentication.
    if request.user.is_authenticated:
        try:
            # Get user profile.
            profile = request.user.profile
        except Exception:
            # Profile missing, logout.
            logout(request)
            return render(request, "mainapp/login.html")

        # Redirect based on role.
        if profile.role == 'student' and profile.student:
            return redirect('student_dashboard')
        elif profile.role == 'tutor':
            return redirect('student_dashboard')
        elif profile.role == 'professor':
            return redirect('teacher_dashboard')
        elif profile.role == 'administrator':
            return redirect('adminage_dashboard')
        else:
            return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Process login form.
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        if not username or not password:
            return render(request, "mainapp/login.html", {"error": "Please provide both username and password."})

        try:
            # Check if user exists.
            User.objects.get(username=username)
        except User.DoesNotExist:
            return render(request, "mainapp/login.html", {"error": "User does not exist"})

        # Authenticate user.
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # Login successful.
            login(request, user)
            profile = request.user.profile

            # Redirect based on role.
            if profile.role == 'student' and profile.student:
                return redirect('student_dashboard')
            elif profile.role == 'tutor':
                return redirect('student_dashboard')
            elif profile.role == 'professor':
                return redirect('teacher_dashboard')
            elif profile.role == 'administrator':
                return redirect('adminage_dashboard')
            else:
                # Unknown role.
                return render(request, "forbidden.html", {"user": request.user, "profile": profile})
        else:
            # Invalid credentials.
            return render(request, "mainapp/login.html", {"error": "Invalid username or password"})

    return render(request, "mainapp/login.html")


def logoutUser(request):
    # Logs out user.
    logout(request)
    return redirect('login')


@login_required
def student_detail(request):
    """
    Student dashboard view with filters.
    Works for both students and tutors.
    """
    profile = request.user.profile

    # Determine student to show.
    student = None
    is_tutor = False
    children_info = []
    selected_child = None
    selected_child_obj = None

    # Handle Tutor role.
    if profile.role in ('tutor', 'legal_tutor'):
        is_tutor = True
        children = profile.children.all()

        if not children.exists():
            # No children assigned.
            context = {
                "is_tutor": True,
                "children_info": [],
            }
            return render(request, "mainapp/student_file.html", context)

        # Get selected child index.
        try:
            selected_child = int(request.GET.get('child', 0))
        except (ValueError, TypeError):
            selected_child = 0

        # Validate index.
        children_list = list(children)
        if selected_child >= len(children_list) or selected_child < 0:
            selected_child = 0

        student = children_list[selected_child]

    elif profile.role == 'student':
        # Handle Student role.
        if not profile.student:
            return render(request, "mainapp/student_profile.html", {"user": request.user, "profile": profile})
        student = profile.student

    else:
        # Unauthorized role.
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # FILTERS: School Year & Trimester
    all_school_years = list(School_year.objects.all().order_by('-year'))

    # Get filter params.
    selected_year_id_raw = request.GET.get('school_year_id')
    selected_trimester_id_raw = request.GET.get('trimester_id')

    # Convert to int.
    selected_year_id = None
    if selected_year_id_raw:
        try:
            candidate = int(selected_year_id_raw)
        except (ValueError, TypeError):
            candidate = None
        if candidate is not None and any(s.SchoolYearID == candidate for s in all_school_years):
            selected_year_id = candidate

    # Get available trimesters.
    available_trimesters = []
    selected_trimester_id = None
    if selected_year_id:
        available_trimesters = Trimester.objects.filter(
            school_year__SchoolYearID=selected_year_id
        ).order_by('Name')
        if selected_trimester_id_raw:
            try:
                t_candidate = int(selected_trimester_id_raw)
            except (ValueError, TypeError):
                t_candidate = None
            if t_candidate is not None and any(t.TrimesterID == t_candidate for t in available_trimesters):
                selected_trimester_id = t_candidate

    else:
        selected_trimester_id = None

    # FILTER GRADES
    grades_qs = Grade.objects.filter(student=student)
    if selected_year_id:
        grades_qs = grades_qs.filter(
            school_year__SchoolYearID=selected_year_id)
    if selected_trimester_id:
        grades_qs = grades_qs.filter(
            trimester__TrimesterID=selected_trimester_id)

    grades = grades_qs.select_related('subject', 'trimester', 'school_year').order_by(
        '-school_year__year', 'trimester__Name', 'subject__Name'
    )

    # FILTER ABSENCES
    ausencias_qs = Ausencias.objects.filter(student=student)
    if selected_year_id:
        ausencias_qs = ausencias_qs.filter(
            school_year__SchoolYearID=selected_year_id)
    if selected_trimester_id:
        ausencias_qs = ausencias_qs.filter(
            trimester__TrimesterID=selected_trimester_id)

    ausencias = ausencias_qs.select_related(
        'subject', 'trimester', 'school_year').order_by('-date_time')

    # PREPARE children_info FOR TUTORS
    if is_tutor:
        for idx, child in enumerate(children):
            # Child grades.
            child_grades = Grade.objects.filter(student=child)
            # Child absences.
            child_ausencias = Ausencias.objects.filter(student=child)

            # Apply filters.
            if selected_year_id:
                child_grades = child_grades.filter(
                    school_year__SchoolYearID=selected_year_id)
                child_ausencias = child_ausencias.filter(
                    school_year__SchoolYearID=selected_year_id)

            if selected_trimester_id:
                child_grades = child_grades.filter(
                    trimester__TrimesterID=selected_trimester_id)
                child_ausencias = child_ausencias.filter(
                    trimester__TrimesterID=selected_trimester_id)

            children_info.append({
                'student': child,
                'grades': child_grades.select_related('subject', 'trimester', 'school_year'),
                'ausencias': child_ausencias.select_related('subject', 'trimester', 'school_year'),
            })

        selected_child_obj = children_info[selected_child] if children_info else None

    # PREPARE CONTEXT
    context = {
        "student": student,
        "grades": grades,
        "ausencias": ausencias,
        "is_tutor": is_tutor,
        "children_info": children_info if is_tutor else None,
        "selected_child": selected_child if is_tutor else None,
        "selected_child_obj": selected_child_obj if is_tutor else None,
        # Filter variables.
        "all_school_years": all_school_years,
        "available_trimesters": available_trimesters,
        "selected_year_id": selected_year_id,
        "selected_trimester_id": selected_trimester_id,
    }

    return render(request, "mainapp/student_file.html", context)


def sort_key_section(course):
    # Helper to sort courses by section (e.g., 1A, 2B).

    section = course.Section
    # Extract number part.
    number_part = int(section[0])
    # Extract letter part.
    letter_part = section[1]

    # Return tuple for sorting.
    return (number_part, letter_part)


@login_required
def teacher_dashboard(request):
    # Get user profile.
    profile = request.user.profile

    # Restrict access to professors.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})
    else:
        # Fetch dashboard data.
        all_students = Students.objects.all().order_by('Name')
        all_grades = Grade.objects.all()
        all_ausencias = Ausencias.objects.all()

        # Get available school years.
        all_school_years = School_year.objects.all().order_by('-year')

        # Get selected school year or default to newest.
        selected_school_year = request.GET.get('school_year')

        if selected_school_year:
            try:
                school_year = School_year.objects.get(
                    SchoolYearID=selected_school_year)
                all_courses = Course.objects.filter(school_year=school_year)
            except School_year.DoesNotExist:
                # Fallback to newest.
                school_year = all_school_years.first()
                all_courses = Course.objects.filter(
                    school_year=school_year) if school_year else Course.objects.none()
        else:
            # Default to newest.
            school_year = all_school_years.first()
            all_courses = Course.objects.filter(
                school_year=school_year) if school_year else Course.objects.none()

    # Sort courses.
    sorted_courses = sorted(all_courses, key=sort_key_section)

    # Categorize courses.
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

    # Prepare context.
    context = {
        "students": all_students,
        "grades": all_grades,
        "ausencias": all_ausencias,
        "courses": all_courses,
        'eso_courses': eso_courses,
        'bachillerato_courses': bachillerato_courses,
        'ib_courses': ib_courses,
        'school_years': all_school_years,
        'selected_school_year': school_year,
    }
    # Render dashboard.
    return render(request, "mainapp/teacher_dashboard.html", context)


@login_required
def section_courses(request, section):
    profile = request.user.profile

    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    sec = (section or '').strip().lower()

    # Map section types.
    mapping = {
        'eso': 'Eso',
        'bachillerato': 'Bachillerato',
        'ib': 'IB',
        'todos': None,
        'all': None,
    }
    target = mapping.get(sec)

    if sec not in mapping:
        return redirect('teacher_dashboard')

    # --- School Year Filter ---

    school_years_qs = School_year.objects.all().order_by('-year')
    selected_year_id = None

    # 1. Get School Year PK.
    selected_year_id_str = request.GET.get('school_year_id')

    if selected_year_id_str:
        try:
            selected_year_id = int(selected_year_id_str)
        except ValueError:
            selected_year_id = None

    # Default to newest if not found.
    if not selected_year_id and school_years_qs.exists():
        selected_year_id = school_years_qs.first().pk

    # --- Main Query ---

    # Base QuerySet: Filter by school year.
    if selected_year_id:
        courses_base_qs = Course.objects.filter(
            school_year_id=selected_year_id)
    else:
        courses_base_qs = Course.objects.none()

    # Filter by Course Type.
    if target is None:
        courses_qs = courses_base_qs
    else:
        courses_qs = courses_base_qs.filter(Tipo=target)

    # Sort and prepare context.
    sorted_courses = sorted(list(courses_qs), key=sort_key_section)

    context = {
        'section_label': section.capitalize(),
        'courses': sorted_courses,
        'is_professor': True,

        # Filter data.
        'school_years': school_years_qs,
        'selected_year_id': selected_year_id,
    }
    return render(request, 'mainapp/section_courses.html', context)


@login_required
def class_dashboard(request, course_id):
    profile = request.user.profile

    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    course = get_object_or_404(Course, CourseID=course_id)

    # Get all Subjects_Courses for this Course.
    subjects_courses = course.subjects_courses_set.all()

    # Filter students by course section.
    students = Students.objects.filter(
        students_courses__course_section=course).distinct().order_by('Name')

    if request.method == 'POST':
        form = AusenciaForm(request.POST, course=course)

        if form.is_valid():
            students_selected = form.cleaned_data.get('students')
            subject = form.cleaned_data.get('subject')
            school_year = form.cleaned_data.get('school_year')
            trimester = form.cleaned_data.get('trimester')
            tipo = form.cleaned_data.get('Tipo')
            date_time = form.cleaned_data.get('date_time')

            created = 0
            for s in students_selected:
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
def download_class_list(request, course_id):
    # Check if user is professor.
    if request.user.profile.role != 'professor':
        return HttpResponse("Access denied.", status=403)

    # 1. Get Course object.
    course = get_object_or_404(Course, CourseID=course_id)

    # Get subject (optional).
    subject_course = Subjects_Courses.objects.filter(course=course).first()

    if not subject_course:
        pass

    # Configure CSV response.
    response = HttpResponse(content_type='text/csv')

    # Generate filename.
    filename = f"{course.Tipo}{course.Section}_import_template.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Initialize CSV writer.
    writer = csv.writer(response)

    # Write header.
    writer.writerow(['Nombre_Estudiante', 'Asignatura', 'Trimestre',
                    'Año_Escolar', 'Nota', 'Tipo_Nota', 'Numero_Tipo_Nota', 'Comentarios'])

    # Get all students in the class.
    students = Students.objects.filter(
        students_courses__course_section=course
    ).distinct().order_by('Name')

    # Get current school year string.
    current_year = timezone.now().year
    school_year_str = f"{current_year}-{current_year + 1}"

    # Iterate over students.
    for student in students:
        # Write row with defaults.
        writer.writerow([
            student.Name,
            '',  # Subject
            '',  # Trimester
            school_year_str,
            '',  # Grade
            'examen',
            '0',
            ''  # Comments
        ])

    return response


@login_required
def student_dashboard_content(request, student_id):
    # Authentication required.
    profile = request.user.profile

    # Restrict to professor.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Get student.
    student = get_object_or_404(Students, StudentID=student_id)

    # --- FILTER LOGIC ---

    # 1. Get filter IDs.
    selected_year_id = request.GET.get('school_year_id')
    selected_trimester_id = request.GET.get('trimester_id')

    # 2. Base QuerySets.
    grades_qs = Grade.objects.filter(student=student)
    ausencias_qs = Ausencias.objects.filter(student=student)

    # 3. Prepare options.
    all_school_years = School_year.objects.all().order_by('-year')
    available_trimesters = Trimester.objects.none()

    # 4. Apply School Year filter.
    if selected_year_id:
        try:
            selected_year_id = int(selected_year_id)
        except (ValueError, TypeError):
            selected_year_id = None

        if selected_year_id:
            grades_qs = grades_qs.filter(school_year_id=selected_year_id)
            ausencias_qs = ausencias_qs.filter(school_year_id=selected_year_id)

            # Populate trimesters for this year.
            available_trimesters = Trimester.objects.filter(
                school_year_id=selected_year_id).order_by('Name')

    # 5. Apply Trimester filter.
    if selected_trimester_id:
        try:
            selected_trimester_id = int(selected_trimester_id)
        except (ValueError, TypeError):
            selected_trimester_id = None

        if selected_trimester_id:
            grades_qs = grades_qs.filter(trimester_id=selected_trimester_id)
            ausencias_qs = ausencias_qs.filter(
                trimester_id=selected_trimester_id)

    # --- END FILTER LOGIC ---

    # Check for return course.
    return_course = request.GET.get('course')

    # Prepare context.
    context = {
        "student": student,
        "grades": grades_qs.order_by('trimester__Name'),
        "ausencias": ausencias_qs.order_by('-date_time'),
        "is_tutor": False,
        "return_course": return_course,

        # Filter context.
        "all_school_years": all_school_years,
        "available_trimesters": available_trimesters,
        "selected_year_id": selected_year_id,
        "selected_trimester_id": selected_trimester_id,
    }
    return render(request, "mainapp/student_dashboard_content.html", context)


@login_required
@login_required
def tutor_dashboard(request):
    """
    Deprecated view. Redirects to student_dashboard which handles
    tutor logic and filtering correctly.
    """
    return redirect('student_dashboard')


@login_required
def grades_csv(request, student_id=None):
    # Generic view to download grades as CSV.
    profile = request.user.profile
    grades = Grade.objects.none()
    filename = "student_data.csv"

    # --- GET FILTERS ---
    selected_year_id = request.GET.get('school_year_id')
    selected_trimester_id = request.GET.get('trimester_id')

    if profile.role == "student" and profile.student:
        # Student: own grades.
        student = profile.student
        grades = Grade.objects.filter(student=student)
        filename = f"{student.Name}_notas.csv"
    elif profile.role == "tutor":
        # Tutor: all children.
        children = list(profile.children.all())
        grades = Grade.objects.filter(student__in=children)
        filename = f"{request.user.username}_notas.csv"
    elif profile.role == "professor":
        if student_id:
            # Professor (specific student).
            student = get_object_or_404(Students, pk=student_id)
            grades = Grade.objects.filter(student=student)
            filename = f"{student.Name}_notas.csv"
        else:
            # Professor (all).
            grades = Grade.objects.all()
            filename = "all_grades.csv"
    else:
        # Access denied.
        return render(request, 'forbidden.html', {"user": request.user, "profile": profile})

    # --- APPLY FILTERS ---
    if selected_year_id:
        try:
            grades = grades.filter(school_year_id=int(selected_year_id))
        except (ValueError, TypeError):
            pass

    if selected_trimester_id:
        try:
            grades = grades.filter(trimester_id=int(selected_trimester_id))
            trim_obj = Trimester.objects.get(pk=int(selected_trimester_id))
            filename = filename.replace(".csv", f"_T{trim_obj.Name}.csv")
        except (ValueError, TypeError, Trimester.DoesNotExist):
            pass

    # Configure CSV response.
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    # Write header.
    writer.writerow(
        ['Estudiante', 'Asignatura', 'Trimestre', 'Año Escolar', 'Nota', 'Tipo de Nota', 'Numero tipo de Nota', 'Comentario'])

    # Write rows.
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
    # Download grades for a specific class.
    profile = request.user.profile

    # Restrict to professor.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Get Course.
    course = get_object_or_404(Course, CourseID=course_id)

    # Filter students by course section.
    students_in_course = Students.objects.filter(
        students_courses__course_section=course).distinct()

    # Get available filters based on existing data.
    subjects_in_course = Subjects.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('Name')
    trimesters = Trimester.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('Name')
    school_years = School_year.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('year')
    grade_types = Grade.objects.filter(
        student__in=students_in_course).values_list('grade_type', flat=True).distinct().order_by('grade_type')

    # Handle POST to generate CSV.
    if request.method == 'POST':
        # Get filters.
        selected_subject_id = request.POST.get('subject')
        selected_trimester_id = request.POST.get('trimester')
        selected_school_year_id = request.POST.get('school_year')
        selected_grade_type = request.POST.get('grade_type')

        # Base grades.
        grades = Grade.objects.filter(student__in=students_in_course)

        # Apply filters.
        if selected_subject_id:
            grades = grades.filter(subject_id=selected_subject_id)
        if selected_trimester_id:
            grades = grades.filter(trimester_id=selected_trimester_id)
        if selected_school_year_id:
            grades = grades.filter(school_year_id=selected_school_year_id)
        if selected_grade_type:
            grades = grades.filter(grade_type=selected_grade_type)

        # Genera el nombre del archivo basado en el curso y los filtros aplicados.
        filename_parts = [course.Tipo, course.Section]
        if selected_subject_id:
            subject = get_object_or_404(Subjects, pk=selected_subject_id)
            filename_parts.append(subject.Name)
        # ... (continúa con la lógica de generación de filename) ...
        # Aquí se necesita completar la lógica original de filename:
        filename = "_".join(filename_parts) + "_grades.csv"

        # Genera el CSV y la respuesta de descarga.
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)

        # Escribe el encabezado CSV
        writer.writerow(
            ['Estudiante', 'Asignatura', 'Trimestre', 'Año Escolar', 'Nota', 'Tipo de Nota', 'Numero_Tipo_Nota', 'Comentario'])

        # Itera y escribe las notas filtradas.
        for grade in grades:
            writer.writerow([
                grade.student.Name,
                grade.subject.Name,
                grade.trimester.Name,
                grade.school_year.year,
                grade.grade,
                grade.grade_type,
                grade.grade_type_number,
                grade.comments
            ])

        return response

    # Petición GET: Muestra el formulario de filtrado para que el profesor elija las opciones.
    context = {
        "course": course,
        "subjects": subjects_in_course,
        "trimesters": trimesters,
        "school_years": school_years,
        "grade_types": grade_types,
    }
    # Renderiza la plantilla con el formulario de descarga.
    return render(request, "mainapp/class_grades_download.html", context)


@login_required
def create_edit_grade(request, grade_id=None, student_id=None):
    """
    Create or edit a grade.
    Pre-selects student and latest school year.
    """
    try:
        profile = request.user.profile
    except User.profile.RelatedObjectDoesNotExist:
        return render(request, "error.html", {"message": "User has no profile."})

    # Restrict to professor.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student_instance = None
    grade_instance = None
    initial_data = {}

    # 1. Get latest school year.
    latest_year = School_year.objects.all().order_by('-year').first()

    # Determine edit or create mode.
    if grade_id:
        # Edit mode.
        grade_instance = get_object_or_404(Grade, id=grade_id)
        student_instance = grade_instance.student
        initial_data['student'] = student_instance.pk
    elif student_id:
        # Create mode.
        student_instance = get_object_or_404(Students, pk=student_id)
        initial_data['student'] = student_instance.pk

        # 2. Set default school year.
        if latest_year:
            initial_data['school_year'] = latest_year.pk

    # Handle POST.
    if request.method == "POST":
        form = GradeForm(request.POST, instance=grade_instance)

        if form.is_valid():
            g = form.save()

            messages.success(request, "Grade saved successfully.")
            return redirect('student_dashboard_content', student_id=student_instance.pk)
    else:
        # GET request.
        form = GradeForm(instance=grade_instance, initial=initial_data)

    context = {
        "form": form,
        "is_edit": grade_instance is not None,
        "student": student_instance,
    }
    return render(request, "mainapp/grade_form.html", context)


# =================================================================
# 2. AJAX VIEW: LOAD TRIMESTERS
# =================================================================

def load_trimesters(request):
    """
    Returns trimesters for a school year as JSON.
    """
    school_year_id = request.GET.get('school_year_id')

    trimesters = Trimester.objects.filter(
        school_year_id=school_year_id).order_by('Name')

    trimester_list = [
        # Use Name (1, 2, 3) as display text.
        {'id': trimester.pk,
         'name': trimester.Name
         }
        for trimester in trimesters
    ]

    return JsonResponse({'trimesters': trimester_list})


@login_required
def create_edit_ausencia(request, ausencia_id=None, student_id=None):
    # Create or edit absence.
    profile = request.user.profile

    # Restrict to professor.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student_instance = None
    ausencia_instance = None

    # Determine edit or create mode.
    if ausencia_id:
        # Edit mode.
        ausencia_instance = get_object_or_404(Ausencias, id=ausencia_id)
        student_instance = ausencia_instance.student
    elif student_id:
        # Create mode.
        student_instance = get_object_or_404(Students, pk=student_id)

    # Handle POST.
    if request.method == "POST":
        form = AusenciaEditForm(request.POST, instance=ausencia_instance)

        if form.is_valid():
            ausencia = form.save(commit=False)

        # Assign student if new.
        if not ausencia_id:
            ausencia.student = student_instance

        ausencia.save()

        messages.success(request, "Absence saved successfully.")
        return redirect('student_dashboard_content', student_id=student_instance.pk)
    else:
        # GET request.
        form = AusenciaEditForm(instance=ausencia_instance)

    context = {
        "form": form,
        "is_edit": ausencia_instance is not None,
        "student": student_instance,
    }
    return render(request, "mainapp/ausencia_form.html", context)


@login_required
def search_students(request):
    profile = request.user.profile

    # Restrict to professor.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Get query and optional course filter.
    query = (request.GET.get('q') or '').strip()
    course_id = request.GET.get('course')

    students_qs = Students.objects.none()

    # Filter by course.
    if course_id:
        try:
            course_obj = Course.objects.get(CourseID=course_id)
            # Filter by Students -> Students_Courses -> Course
            students_qs = Students.objects.filter(
                students_courses__course_section=course_obj
            ).distinct()
        except Course.DoesNotExist:
            students_qs = Students.objects.none()
    else:
        # No course filter, start with all.
        students_qs = Students.objects.all()

    def _strip_accents(text):
        if not text:
            return ''
        nkfd = unicodedata.normalize('NFKD', text)
        return ''.join([c for c in nkfd if not unicodedata.combining(c)])

    # Filter by query.
    if query:
        qnorm = _strip_accents(query).lower()

        # Try DB-level unaccent optimization.
        try:
            from django.db.models import Func, F

            students_qs_candidate = students_qs.annotate(
                name_unaccent=Func(F('Name'), function='unaccent'),
                email_unaccent=Func(F('Email'), function='unaccent'),
            ).filter(
                Q(name_unaccent__icontains=qnorm) | Q(
                    email_unaccent__icontains=qnorm)
            ).order_by('Name')

            # Test execution.
            try:
                _ = list(students_qs_candidate[:1])
                students_qs = students_qs_candidate
            except Exception:
                raise
        except Exception:
            # Fallback to Python filtering.
            matched = []
            for s in students_qs:
                name_val = getattr(s, 'Name', '') or ''
                email_val = getattr(s, 'Email', '') or ''
                if qnorm in _strip_accents(name_val).lower() or qnorm in _strip_accents(email_val).lower():
                    matched.append(s)
            students_qs = matched
    else:
        if not course_id:
            students_qs = Students.objects.none()

    # Prepare results.
    results = []
    for s in students_qs:
        # Get courses.
        student_courses_relations = Students_Courses.objects.filter(
            student=s
        ).select_related('course_section')

        courses = [
            sc.course_section
            for sc in student_courses_relations
            if sc.course_section is not None
        ]

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

    # Restrict to professor.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Get Course.
    course = None
    if course_id:
        course = get_object_or_404(Course, CourseID=course_id)

    # Handle POST (CSV upload).
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')

        # Basic validation.
        if not csv_file:
            messages.error(request, 'Por favor selecciona un archivo CSV.')
            return render(request, 'mainapp/import_grades.html', {'course': course})

        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'El archivo debe ser un CSV.')
            return render(request, 'mainapp/import_grades.html', {'course': course})

        # Counters.
        created_count = 0
        updated_count = 0
        error_count = 0
        errors = []

        try:
            # Read CSV.
            csv_content = csv_file.read().decode('utf-8')
            reader = csv.DictReader(csv_content.splitlines())

            # Iterate rows.
            for row_num, row in enumerate(reader, start=2):
                try:
                    # Parse values.
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

                    # Get Student and Subject.
                    student = Students.objects.get(Name=student_name)
                    subject = Subjects.objects.get(Name=subject_name)

                    # Get or create School Year.
                    school_year, _ = School_year.objects.get_or_create(
                        year=school_year_str,
                        defaults={'year': school_year_str}
                    )

                    # Get or create Trimester.
                    trimester, _ = Trimester.objects.get_or_create(
                        Name=int(trimester_name),
                        school_year=school_year,
                        defaults={'Name': int(
                            trimester_name), 'school_year': school_year}
                    )

                    # Update or Create Grade.
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
                        f"Row {row_num}: Student '{student_name}' not found")
                    error_count += 1
                except Subjects.DoesNotExist:
                    errors.append(
                        f"Row {row_num}: Subject '{subject_name}' not found")
                    error_count += 1
                except Exception as e:
                    errors.append(f"Row {row_num}: {str(e)}")
                    error_count += 1

            # Show summary.
            if created_count > 0:
                messages.success(request, f'✓ Created: {created_count}')
            if updated_count > 0:
                messages.info(
                    request, f'↻ Updated: {updated_count}')
            if error_count > 0:
                messages.error(request, f'❌ Errors: {error_count}')
                for error in errors[:10]:
                    messages.error(request, error)
                if len(errors) > 10:
                    messages.error(
                        request, f'... and {len(errors) - 10} more errors')

        except Exception as e:
            messages.error(request, f'Error processing file: {str(e)}')

    context = {
        'course': course,
    }
    return render(request, 'mainapp/import_grades.html', context)


@login_required
def adminage_dashboard_view(request):
    profile = request.user.profile
    # Restrict to administrator.
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    context = {
        'title': 'School Admin Dashboard',
        'school_years': School_year.objects.all().order_by('-year')
    }
    return render(request, "adminage/adminage_dashboard.html", context)


# =======================================================
# --- VIEW 1: CREATE SCHOOL YEAR ---
# =======================================================
@login_required
def create_school_year_view(request):
    profile = request.user.profile
    # Restrict to administrator.
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Handle POST.
    if request.method == 'POST':
        form = SchoolYearForm(request.POST)
        if form.is_valid():
            # 1. Create School_year.
            school_year_obj = form.save()

            # 2. Create 3 Trimesters.
            trimestres_a_crear = [
                Trimester(Name=1, school_year=school_year_obj),
                Trimester(Name=2, school_year=school_year_obj),
                Trimester(Name=3, school_year=school_year_obj),
            ]
            # Bulk create.
            Trimester.objects.bulk_create(trimestres_a_crear)

            messages.success(
                request, f"School Year {school_year_obj.year} created with 3 trimesters.")

            # 3. Redirect to course creation.
            url = reverse('create_courses_sections')
            return redirect(f'{url}?school_year_id={school_year_obj.pk}')
        else:
            messages.error(
                request, "Error creating School Year.")
    else:
        # GET request.
        form = SchoolYearForm()

    context = {
        'form': form,
        'title': 'Create New School Year'
    }
    return render(request, "adminage/create_school_year.html", context)


# =======================================================
# --- VIEW 2: CREATE COURSE SECTIONS (Multi-Step) ---
# =======================================================
@login_required
def create_courses_sections_view(request):
    profile = request.user.profile
    # Restrict to administrator.
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Get School Year ID.
    school_year_id = request.GET.get('school_year_id')

    # Validate flow.
    if not school_year_id:
        messages.error(request, "Start by defining a School Year.")
        return redirect('adminage_dashboard')

    # Get School Year object.
    try:
        school_year = School_year.objects.get(pk=school_year_id)
    except School_year.DoesNotExist:
        raise Http404("School Year not found.")

    context = {'school_year': school_year}

    # --- STEP 1 POST (Select Type) ---
    if request.method == 'POST' and request.POST.get('step') == 'select_type':

        form_main = CourseCreationForm(
            request.POST,
            initial_school_year_id=school_year_id,
            course_type_initial=request.POST.get('course_tipo')
        )

        if not form_main.is_valid():
            messages.error(request, "Validation error.")
            context['form'] = form_main
            return render(request, "adminage/create_courses_step1.html", context)

        course_tipo = form_main.cleaned_data['course_tipo']
        return _render_step2(request, course_tipo, school_year, form_main)

    # --- STEP 2 POST (Confirm Sections) ---
    elif request.method == 'POST' and request.POST.get('step') == 'confirm_sections':

        course_tipo = request.POST.get('course_tipo')

        form_main = CourseCreationForm(
            request.POST,
            initial_school_year_id=school_year_id,
            course_type_initial=course_tipo
        )

        if not form_main.is_valid():
            messages.error(request, "Validation error. Restart.")
            return redirect('adminage_dashboard')

        CourseFormSet = formset_factory(CourseSectionForm, extra=0)
        formset = CourseFormSet(request.POST)

        if formset.is_valid():
            num_created = 0

            for form_section in formset:
                if form_section.cleaned_data:
                    main_course_name = form_section.cleaned_data['main_course_name']
                    num_subsections = form_section.cleaned_data['num_subsections']
                    # Generate letters (A=65, B=66...).
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
                request, f"{num_created} sections created for {course_tipo} ({school_year}).")

            return redirect('adminage_dashboard')

        else:
            messages.error(request, "Please correct errors.")
            return _render_step2(request, course_tipo, school_year, form_main, formset=formset)

    # --- STEP 1 GET (Initial Load) ---
    else:
        form = CourseCreationForm(
            initial_school_year_id=school_year_id,
            initial={'school_year': school_year}
        )
        context['form'] = form
        return render(request, "adminage/create_courses_step1.html", context)


# Helper to render Step 2
def _render_step2(request, course_tipo, school_year, form_main, formset=None):
    profile = request.user.profile
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    if not formset:
        CourseFormSet = formset_factory(CourseSectionForm, extra=0)
        initial_data = []
        # Use MAIN_COURSES global.
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
        'title': f"Define Sections for {course_tipo} ({school_year})"
    }
    return render(request, "adminage/create_courses_step2.html", context)


@login_required
def assign_subjects_view(request):
    """
    Complex view to assign subject/teacher to multiple trimesters,
    optionally defining a subset of students.
    """
    course_types = Course.COURSE_TYPE_CHOICES

    # 1. INITIALIZATION
    # Get latest school year default.
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

    # Load objects if school year valid.
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
            messages.error(request, "Invalid School Year.")
            return redirect('assign_subjects')

    # Get student links if course selected.
    if selected_course_id:
        try:
            target_course = Course.objects.get(pk=selected_course_id)
            course_students_links = Students_Courses.objects.filter(
                course_section=target_course
            ).select_related('student').order_by('student__Name')
        except Course.DoesNotExist:
            messages.warning(
                request, "Invalid Course ID.")
            selected_course_id = None

    # 2. HANDLE POST (Create/Update Assignment)
    if request.method == 'POST':

        selected_course_id = request.POST.get('course_id')
        school_year_id_post = request.POST.get('school_year_id')
        final_school_year_id = school_year_id_post or school_year_id

        if not selected_course_id or not final_school_year_id:
            messages.error(
                request, "Error: Select course and school year.")
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}')

        try:
            target_course = Course.objects.get(pk=selected_course_id)
        except Course.DoesNotExist:
            messages.error(request, "Invalid Course.")
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}')

        form = SubjectAssignmentForm(request.POST)
        form.fields['subject'].queryset = Subjects.objects.all().order_by(
            'Name')
        form.fields['teacher'].queryset = Teachers.objects.all().order_by(
            'Name')

        if form.is_valid():
            # --- TRIMESTER LOGIC ---
            trimester_ids_selected = request.POST.getlist(
                'trimesters_selected')

            if not trimester_ids_selected:
                messages.error(
                    request, "Error: Select at least one trimester.")
                current_form = form
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            try:
                selected_trimesters = Trimester.objects.filter(
                    pk__in=trimester_ids_selected,
                    school_year__pk=final_school_year_id
                )
            except ValueError:
                messages.error(
                    request, "Error: Invalid Trimester IDs.")
                current_form = form
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            # --- STUDENT LOGIC ---
            # Get selected student link IDs.
            assigned_students_courses_ids_selected = request.POST.getlist(
                'student_links_selected')

            try:
                assigned_students_courses_ids = [
                    int(pk) for pk in assigned_students_courses_ids_selected if pk]
            except ValueError:
                messages.error(
                    request, "Error: Invalid Student IDs.")
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            student_count = len(assigned_students_courses_ids)

            subject = form.cleaned_data['subject']
            teacher = form.cleaned_data['teacher']
            newly_created_objects = []

            # 1. Create/Get Subjects_Courses for each TRIMESTER.
            for trimester in selected_trimesters:
                assignment, created = Subjects_Courses.objects.get_or_create(
                    subject=subject,
                    course=target_course,
                    trimester=trimester,
                    defaults={'teacher': teacher}
                )
                if not created and assignment.teacher != teacher:
                    assignment.teacher = teacher
                    assignment.save()

                newly_created_objects.append(assignment)

            # 2. Assign student links.
            if student_count > 0:
                for assignment in newly_created_objects:
                    assignment.assigned_course_sections.set(
                        assigned_students_courses_ids)

                messages.success(
                    request, f"Assignment of {subject.Name} created/updated for {len(newly_created_objects)} trimester(s) and {student_count} students.")

            else:
                # If no students selected, clear ManyToMany.
                for assignment in newly_created_objects:
                    assignment.assigned_course_sections.clear()
                messages.warning(
                    request, f"Asignación de {subject.Name} creada para {len(newly_created_objects)} trimestre(s), pero no se seleccionó ningún estudiante.")

            # Redirige para limpiar la petición POST y mantener los filtros GET en la URL.
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

        else:
            # Si el formulario falla, re-renderiza.
            messages.error(
                request, "Error en el formulario de asignación. Revise Asignatura y Profesor.")
            current_form = form
            # Recarga la lista de estudiantes para que la plantilla los muestre de nuevo.
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
        # Lista de registros de estudiante-curso
        'course_students_links': course_students_links,
    }
    return render(request, "adminage/assign_subjects.html", context)


# =======================================================
# B. Endpoint AJAX para Carga Dinámica de Secciones
# =======================================================
@login_required
def load_course_sections(request):
    """
    Endpoint AJAX para devolver los niveles o las secciones finales de un curso en cascada.
    Se utiliza en el formulario de asignación de asignaturas y creación de estudiantes.
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    # Puede ser None o el número del nivel (ej: '1').
    level = request.GET.get('level')

    # Validación mínima de parámetros.
    if not school_year_id or not course_type:
        return JsonResponse({'data': []})

    try:
        # Filtra todos los cursos disponibles para el año y tipo seleccionados.
        sections_query = Course.objects.filter(
            school_year__pk=school_year_id,
            Tipo=course_type,
        ).order_by('Section')

        # --- MODO 1: Devolver NIVELES (al seleccionar Tipo) ---
        if not level:
            # Obtiene todos los nombres de sección existentes (ej: '1A', '2B').
            all_sections_names = sections_query.values_list(
                'Section', flat=True).distinct()

            # Usa el diccionario de niveles principales.
            main_levels = MAIN_COURSES.get(course_type, [])
            available_levels = []

            # Filtra solo los niveles principales que tienen al menos una subsección creada.
            for lvl in main_levels:
                lvl_str = str(lvl)
                if any(s.startswith(lvl_str) for s in all_sections_names):
                    available_levels.append({
                        'value': lvl_str,
                        'text': f'{lvl_str}º {course_type}'
                    })

            # Retorna los niveles disponibles.
            return JsonResponse({'mode': 'LEVELS', 'data': available_levels})

        # --- MODO 2: Devolver SECCIONES FINALES (al seleccionar Nivel) ---
        else:
            # Filtra las secciones que empiezan con el nivel seleccionado (ej: '1A', '1B').
            final_sections = sections_query.filter(
                Section__startswith=level
            ).values('CourseID', 'Section')

            sections_data = []
            for s in final_sections:
                sections_data.append({
                    'id': s['CourseID'],
                    'text': s['Section'],
                })

            # Retorna las secciones finales.
            return JsonResponse({'mode': 'SECTIONS', 'data': sections_data})

    except Exception as e:
        print(f"!!! ERROR FATAL EN load_course_sections (AJAX) !!!: {e}")
        return JsonResponse({'error': f'Error interno del servidor: {str(e)}'}, status=500)


# =======================================================
# --- SINGLE VIEW: CREATE STUDENT AND ASSIGN CLASS ---
# =======================================================
@login_required
def create_and_assign_student_view(request):
    """
    Creates a new student and assigns them to a course section.
    """

    course_types = Course.COURSE_TYPE_CHOICES

    # Get latest school year ID for AJAX filters.
    latest_school_year = School_year.objects.order_by(
        '-year').only('pk').first()
    latest_school_year_id = str(
        latest_school_year.pk) if latest_school_year else ''

    current_form = StudentCreationForm()

    if request.method == 'POST':
        current_form = StudentCreationForm(request.POST)
        # Selected course section ID.
        course_id = request.POST.get('course_id')

        if current_form.is_valid():

            if not course_id:
                messages.error(
                    request, "Error: Select a course section to assign.")
            else:
                try:
                    # Get selected Course.
                    target_course = Course.objects.get(pk=course_id)
                except Course.DoesNotExist:
                    messages.error(
                        request, "Error: Invalid course section.")
                else:
                    # 1. Create Student.
                    new_student = current_form.save()

                    # 2. Create Relation (Students_Courses).
                    Students_Courses.objects.create(
                        student=new_student,
                        course_section=target_course
                    )

                    messages.success(
                        request, f"Student '{new_student.Name}' created and assigned to {target_course.Section}.")

                    # Redirect to clear form.
                    return redirect('create_and_assign_student')

        else:
            messages.error(
                request, "Error: Check student data (Name/Email).")

    context = {
        'title': 'Create Student and Assign Class',
        'form': current_form,
        'course_types': course_types,
        'current_school_year_id': latest_school_year_id,
        # Pass selected course ID if POST failed.
        'selected_course_id': request.POST.get('course_id', ''),
    }
    return render(request, "adminage/create_and_assign_student.html", context)


def reassign_students(request):
    """
    Main view to reassign students from one class to another.
    """
    if request.method == 'POST':
        # Process reassignment.
        # List of "student_id:course_id"
        assignments = request.POST.getlist('assignments')

        success_count = 0
        error_count = 0

        for assignment in assignments:
            if not assignment or assignment == ':':
                continue

            try:
                student_id, new_course_id = assignment.split(':')
                student = Students.objects.get(StudentID=student_id)
                new_course = Course.objects.get(CourseID=new_course_id)

                # Find existing assignment.
                existing_assignment = Students_Courses.objects.filter(
                    student=student
                ).first()

                if existing_assignment:
                    # Update existing.
                    existing_assignment.course_section = new_course
                    existing_assignment.save()
                else:
                    # Create new if not exists.
                    Students_Courses.objects.create(
                        student=student,
                        course_section=new_course
                    )

                success_count += 1

            except Exception as e:
                error_count += 1
                messages.error(
                    request, f"Error reassigning student: {str(e)}")

        if success_count > 0:
            messages.success(
                request, f"{success_count} student(s) reassigned successfully.")
        if error_count > 0:
            messages.warning(
                request, f"{error_count} error(s) during reassignment.")

        return redirect('reassign_students')

    # GET: Show form.
    school_years = School_year.objects.all().order_by('-year')
    course_types = Course.COURSE_TYPE_CHOICES

    context = {
        'school_years': school_years,
        'course_types': course_types,
    }

    return render(request, 'reassign_students.html', context)


def ajax_get_course_numbers(request):
    """
    AJAX endpoint to get available course numbers
    based on selected school year and type.
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')

    if not school_year_id or not course_type:
        return JsonResponse({'numbers': [], 'error': 'Missing parameters'})

    try:
        # Get matching courses.
        courses = Course.objects.filter(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type
        )

        # Extract unique numbers from Section field.
        sections = courses.values_list('Section', flat=True)

        # Try extracting first numeric character.
        numbers = set()
        for section in sections:
            if section:
                # Extract digits from start of string.
                num = ''
                for char in section:
                    if char.isdigit():
                        num += char
                    else:
                        break
                if num:
                    numbers.add(num)

        numbers_list = sorted(list(numbers))

        return JsonResponse({'numbers': numbers_list})

    except Exception as e:
        return JsonResponse({'numbers': [], 'error': str(e)})


def ajax_get_course_sections(request):
    """
    AJAX endpoint to get available sections (letters)
    based on year, type, and course number.
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    course_number = request.GET.get('course_number')

    if not all([school_year_id, course_type, course_number]):
        return JsonResponse({'sections': [], 'error': 'Missing parameters'})

    try:
        # Find sections starting with selected number.
        courses = Course.objects.filter(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type,
            Section__startswith=course_number
        )

        # Extract letters (everything after number).
        sections = set()
        for course in courses:
            section = course.Section
            if section:
                # Skip initial digits and take the rest.
                letter_part = ''
                skip_digits = True
                for char in section:
                    if skip_digits and char.isdigit():
                        continue
                    skip_digits = False
                    letter_part += char

                if letter_part:
                    sections.add(letter_part)

        sections_list = sorted(list(sections))

        return JsonResponse({'sections': sections_list})

    except Exception as e:
        return JsonResponse({'sections': [], 'error': str(e)})


def ajax_get_students(request):
    """
    AJAX endpoint to get students of a specific class.
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    course_number = request.GET.get('course_number')
    section_letter = request.GET.get('section_letter')

    if not all([school_year_id, course_type, course_number, section_letter]):
        return JsonResponse({'students': [], 'error': 'Missing parameters'})

    # Build full Section (e.g., '1A').
    section_full = f"{course_number}{section_letter}"

    # Find specific course.
    try:
        course = Course.objects.get(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type,
            Section=section_full
        )

        # Get students assigned to this course.
        student_courses = Students_Courses.objects.filter(
            course_section=course
        ).select_related('student')

        students = [
            {
                'id': sc.student.StudentID,
                'name': sc.student.Name,
                'email': sc.student.Email
            }
            for sc in student_courses
        ]

        return JsonResponse({'students': students, 'course_id': course.CourseID})

    except Course.DoesNotExist:
        return JsonResponse({'students': [], 'error': 'Course not found'})
    except Exception as e:
        return JsonResponse({'students': [], 'error': str(e)})


def ajax_get_destination_courses(request):
    """
    AJAX endpoint to get destination course ID.
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    course_number = request.GET.get('course_number')
    section_letter = request.GET.get('section_letter')

    if not all([school_year_id, course_type, course_number, section_letter]):
        return JsonResponse({'course_id': None, 'error': 'Missing parameters'})

    section_full = f"{course_number}{section_letter}"

    try:
        course = Course.objects.get(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type,
            Section=section_full
        )
        return JsonResponse({'course_id': course.CourseID})
    except Course.DoesNotExist:
        return JsonResponse({'course_id': None, 'error': 'Course not found'})
    except Exception as e:
        return JsonResponse({'course_id': None, 'error': str(e)})
