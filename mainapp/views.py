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
    # Verifica si el usuario ya está autenticado.
    if request.user.is_authenticated:
        try:
            # Intenta obtener el perfil asociado al usuario autenticado.
            profile = request.user.profile
        except Exception:
            # Si el perfil no existe por alguna razón, cierra la sesión.
            logout(request)
            # Muestra la página de inicio de sesión.
            return render(request, "mainapp/login.html")

        # Redirecciona al usuario a su panel de control específico según su rol.
        if profile.role == 'student' and profile.student:
            return redirect('student_dashboard')
        elif profile.role == 'tutor':
            return redirect('tutor_dashboard')
        elif profile.role == 'professor':
            return redirect('teacher_dashboard')
        elif profile.role == 'administrator':
            return redirect('adminage_dashboard')
        else:
            # Si el rol existe pero no es válido, muestra una página de acceso prohibido.
            return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Procesa el formulario de inicio de sesión (método POST).
    if request.method == "POST":
        # Extrae el nombre de usuario y la contraseña del formulario.
        username = request.POST.get("username")
        password = request.POST.get("password")

        # Verifica que ambos campos hayan sido completados.
        if not username or not password:
            return render(request, "mainapp/login.html", {"error": "Please provide both username and password."})

        try:
            # Verifica si el usuario existe en la base de datos antes de intentar la autenticación.
            User.objects.get(username=username)
        except User.DoesNotExist:
            # Si el usuario no existe, informa el error.
            return render(request, "mainapp/login.html", {"error": "User does not exist"})

        # Intenta autenticar al usuario usando las credenciales proporcionadas.
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # Si la autenticación es exitosa, inicia la sesión del usuario.
            login(request, user)
            profile = request.user.profile

            # Redirecciona al panel de control según el rol.
            if profile.role == 'student' and profile.student:
                return redirect('student_dashboard')
            elif profile.role == 'tutor':
                return redirect('tutor_dashboard')
            elif profile.role == 'professor':
                return redirect('teacher_dashboard')
            else:
                # Caso de rol no reconocido después del login.
                return render(request, "forbidden.html", {"user": request.user, "profile": profile})
        else:
            # Si el autenticador retorna None, las credenciales son inválidas.
            return render(request, "mainapp/login.html", {"error": "Invalid username or password"})

    # Si la petición es GET (o cualquier otro método), simplemente muestra el formulario de inicio de sesión.
    return render(request, "mainapp/login.html")


def logoutUser(request):
    # Cierra la sesión del usuario.
    logout(request)
    # Redirecciona al usuario a la página de inicio de sesión.
    return redirect('login')


@login_required
def student_detail(request):
    """
    Vista para mostrar el dashboard del estudiante con filtros de año escolar y trimestre.
    Funciona tanto para estudiantes como para tutores viendo datos de sus hijos.
    """
    profile = request.user.profile

    # Determinar qué estudiante mostrar
    student = None
    is_tutor = False
    children_info = []
    selected_child = None
    selected_child_obj = None

    # Aceptar tanto 'tutor' como 'legal_tutor' (si el valor en DB difiere)
    if profile.role in ('tutor', 'legal_tutor'):
        # Caso: Usuario es tutor
        is_tutor = True
        children = profile.children.all()

        if not children.exists():
            # No tiene hijos asignados
            context = {
                "is_tutor": True,
                "children_info": [],
            }
            return render(request, "mainapp/student_file.html", context)

        # Obtener el índice del hijo seleccionado (parámetro GET 'child')
        try:
            selected_child = int(request.GET.get('child', 0))
        except (ValueError, TypeError):
            selected_child = 0

        # Validar que el índice esté en rango
        children_list = list(children)
        if selected_child >= len(children_list) or selected_child < 0:
            selected_child = 0

        student = children_list[selected_child]

    elif profile.role == 'student':
        # Caso: Usuario es estudiante
        if not profile.student:
            # Renderizar perfil del estudiante si no está vinculado a un registro
            return render(request, "mainapp/student_profile.html", {"user": request.user, "profile": profile})
        student = profile.student

    else:
        # Rol no autorizado
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # FILTROS DE AÑO ESCOLAR Y TRIMESTRE
    all_school_years = list(School_year.objects.all().order_by('-year'))

    # Obtener parámetros de filtro desde GET
    selected_year_id_raw = request.GET.get('school_year_id')
    selected_trimester_id_raw = request.GET.get('trimester_id')

    # Convertir a enteros si existen
    selected_year_id = None
    if selected_year_id_raw:
        try:
            candidate = int(selected_year_id_raw)
        except (ValueError, TypeError):
            candidate = None
        if candidate is not None and any(s.SchoolYearID == candidate for s in all_school_years):
            selected_year_id = candidate

    # Calcular trimestres disponibles si hay año seleccionado
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

    # Si no se dejó seleccionar año válido, no hay trimestre seleccionado
    else:
        selected_trimester_id = None

    # FILTRAR CALIFICACIONES
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

    # FILTRAR AUSENCIAS
    ausencias_qs = Ausencias.objects.filter(student=student)
    if selected_year_id:
        ausencias_qs = ausencias_qs.filter(
            school_year__SchoolYearID=selected_year_id)
    if selected_trimester_id:
        ausencias_qs = ausencias_qs.filter(
            trimester__TrimesterID=selected_trimester_id)

    ausencias = ausencias_qs.select_related(
        'subject', 'trimester', 'school_year').order_by('-date_time')

    # PREPARAR children_info PARA TUTORES
    if is_tutor:
        for idx, child in enumerate(children):
            # NOTAS DEL HIJO
            child_grades = Grade.objects.filter(student=child)
            # AUSENCIAS DEL HIJO
            child_ausencias = Ausencias.objects.filter(student=child)

            # Aplicar los mismos filtros a cada hijo
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

    # PREPARAR CONTEXTO
    context = {
        "student": student,
        "grades": grades,
        "ausencias": ausencias,
        "is_tutor": is_tutor,
        "children_info": children_info if is_tutor else None,
        "selected_child": selected_child if is_tutor else None,
        "selected_child_obj": selected_child_obj if is_tutor else None,
        # Variables para los filtros
        "all_school_years": all_school_years,
        "available_trimesters": available_trimesters,
        "selected_year_id": selected_year_id,
        "selected_trimester_id": selected_trimester_id,
    }

    return render(request, "mainapp/student_file.html", context)


def sort_key_section(course):
    # Función auxiliar para ordenar los cursos por sección de forma lógica.
    # Asume que la sección sigue un patrón como "1A", "2B", etc.

    section = course.Section
    # Extrae la parte numérica (e.g., '1' de '1A').
    number_part = int(section[0])
    # Extrae la parte de la letra (e.g., 'A' de '1A').
    letter_part = section[1]

    # Retorna una tupla para que el método sorted() ordene primero por número y luego por letra.
    return (number_part, letter_part)


@login_required
def teacher_dashboard(request):
    # Obtiene el perfil del usuario.
    profile = request.user.profile

    # Restringe el acceso: solo los usuarios con rol 'professor' pueden acceder al dashboard.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})
    else:
        # Recupera todos los datos necesarios para el dashboard: estudiantes, calificaciones, ausencias.
        all_students = Students.objects.all().order_by('Name')
        all_grades = Grade.objects.all()
        all_ausencias = Ausencias.objects.all()

        # Obtiene todos los años escolares disponibles, ordenados descendentemente.
        all_school_years = School_year.objects.all().order_by('-year')

        # Obtiene el año escolar seleccionado del GET parameter, o usa el más reciente por defecto.
        selected_school_year = request.GET.get('school_year')

        if selected_school_year:
            try:
                # Intenta obtener el año escolar específico
                school_year = School_year.objects.get(
                    SchoolYearID=selected_school_year)
                all_courses = Course.objects.filter(school_year=school_year)
            except School_year.DoesNotExist:
                # Si no existe, usa el más reciente
                school_year = all_school_years.first()
                all_courses = Course.objects.filter(
                    school_year=school_year) if school_year else Course.objects.none()
        else:
            # Por defecto, obtiene el año escolar más reciente
            school_year = all_school_years.first()
            all_courses = Course.objects.filter(
                school_year=school_year) if school_year else Course.objects.none()

    # Ordena la lista de cursos en Python utilizando la función sort_key_section.
    sorted_courses = sorted(all_courses, key=sort_key_section)

    # Inicializa listas vacías para categorizar los cursos por tipo (ESO, Bachillerato, IB).
    eso_courses = []
    bachillerato_courses = []
    ib_courses = []

    # Itera sobre los cursos ordenados y los clasifica.
    for course in sorted_courses:
        if course.Tipo == "Eso":
            eso_courses.append(course)
        elif course.Tipo == "Bachillerato":
            bachillerato_courses.append(course)
        elif course.Tipo == "IB":
            ib_courses.append(course)

    # Prepara el contexto para la plantilla, incluyendo los cursos clasificados y los años escolares.
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
    # Renderiza la plantilla del panel de control del profesor.
    return render(request, "mainapp/teacher_dashboard.html", context)


@login_required
def section_courses(request, section):
    profile = request.user.profile

    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    sec = (section or '').strip().lower()

    # Define el mapeo para el filtro por Tipo de Curso
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

    # --- Lógica de Filtro por Año Escolar (NUEVO) ---

    school_years_qs = School_year.objects.all().order_by('-year')
    selected_year_id = None

    # 1. Determinar el PK del año escolar a usar
    selected_year_id_str = request.GET.get('school_year_id')

    if selected_year_id_str:
        try:
            selected_year_id = int(selected_year_id_str)
        except ValueError:
            selected_year_id = None

    # Si no se encontró un ID válido en la URL, usamos el PK del más reciente.
    if not selected_year_id and school_years_qs.exists():
        selected_year_id = school_years_qs.first().pk

    # --- Consulta Principal Filtrada por Año ---

    # Base QuerySet: Filtrar por el año escolar seleccionado
    if selected_year_id:
        # 🚨 Aplicar el filtro de año a TODOS los cursos
        courses_base_qs = Course.objects.filter(
            school_year_id=selected_year_id)
    else:
        courses_base_qs = Course.objects.none()

    # Aplicar el filtro por Tipo de Curso (section)
    if target is None:
        # Si es 'todos', usa la base (ya filtrada por año)
        courses_qs = courses_base_qs
    else:
        # Filtra por Tipo sobre la base filtrada por año
        courses_qs = courses_base_qs.filter(Tipo=target)

    # Ordenar y preparar el contexto
    sorted_courses = sorted(list(courses_qs), key=sort_key_section)

    context = {
        'section_label': section.capitalize(),
        'courses': sorted_courses,
        'is_professor': True,

        # 🆕 Datos del filtro para la plantilla
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

    # Obtiene todas las instancias de Subjects_Courses relacionadas con este Course.
    subjects_courses = course.subjects_courses_set.all()

    # CORRECCIÓN APLICADA AQUÍ:
    # Filtra usando students_courses__course_section (Students -> Students_Courses -> Course).
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
    # Requiere autenticación.
    # Verifica que el usuario tenga el rol 'professor'.
    if request.user.profile.role != 'professor':
        return HttpResponse("Acceso denegado.", status=403)

    # 1. Recuperamos el objeto Course principal (usando get_object_or_404 para robustez).
    course = get_object_or_404(Course, CourseID=course_id)

    # Intentamos obtener una instancia de Subjects_Courses para obtener el nombre del curso,
    # aunque no usaremos esta instancia para filtrar a los estudiantes.
    subject_course = Subjects_Courses.objects.filter(course=course).first()

    # Si no hay ninguna asignatura asignada, aún podemos nombrar el archivo.
    if not subject_course:
        pass

    # Configura la respuesta HTTP para descargar un archivo CSV.
    response = HttpResponse(content_type='text/csv')

    # Genera el nombre del archivo CSV basado en el Tipo y Section del Course.
    filename = f"{course.Tipo}{course.Section}_import_template.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    # Inicializa el escritor CSV.
    writer = csv.writer(response)

    # Escribe la fila de encabezado.
    writer.writerow(['Nombre_Estudiante', 'Asignatura', 'Trimestre',
                    'Año_Escolar', 'Nota', 'Tipo_Nota', 'Numero_Tipo_Nota', 'Comentarios'])

    # *** FILTRADO CORREGIDO PARA OBTENER TODOS LOS ESTUDIANTES DE LA CLASE ***
    # Se filtra el modelo Students por la relación Students -> Students_Courses -> Course.
    students = Students.objects.filter(
        students_courses__course_section=course
    ).distinct().order_by('Name')

    # Calcula el string del año escolar actual (e.g., '2025-2026') para prellenar la plantilla.
    current_year = timezone.now().year
    school_year_str = f"{current_year}-{current_year + 1}"

    # Itera sobre TODOS los estudiantes encontrados.
    for student in students:
        # Escribe la fila con el nombre del estudiante y valores por defecto.
        writer.writerow([
            student.Name,  # Nombre_Estudiante
            '',  # Asignatura (vacío)
            '',  # Trimestre (vacío)
            school_year_str,  # Año_Escolar (predeterminado)
            '',  # Nota (vacío)
            'examen',  # Tipo_Nota (valor por defecto)
            '0',  # Numero_Tipo_Nota (valor por defecto)
            ''  # Comentarios (vacío)
        ])

    # Retorna la respuesta HTTP que fuerza la descarga del archivo.
    return response


@login_required
def student_dashboard_content(request, student_id):
    # Requiere autenticación.
    profile = request.user.profile

    # Restricción de rol: solo el 'professor' puede acceder a esta vista.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Recupera el objeto Students usando el student_id de la URL.
    student = get_object_or_404(Students, StudentID=student_id)

    # --- INICIO DE LA LÓGICA DE FILTROS ---

    # 1. Obtener los IDs de los filtros de los parámetros GET de la URL
    selected_year_id = request.GET.get('school_year_id')
    selected_trimester_id = request.GET.get('trimester_id')

    # 2. Empezar con los QuerySets base
    grades_qs = Grade.objects.filter(student=student)
    ausencias_qs = Ausencias.objects.filter(student=student)

    # 3. Preparar las opciones para los desplegables
    all_school_years = School_year.objects.all().order_by('-year')
    available_trimesters = Trimester.objects.none()  # Vacío por defecto

    # 4. Aplicar filtro de Año Escolar (si se seleccionó)
    if selected_year_id:
        try:
            selected_year_id = int(selected_year_id)
        except (ValueError, TypeError):
            selected_year_id = None

        if selected_year_id:
            grades_qs = grades_qs.filter(school_year_id=selected_year_id)
            ausencias_qs = ausencias_qs.filter(school_year_id=selected_year_id)

            # Poblar el desplegable de trimestres SOLO para ese año
            available_trimesters = Trimester.objects.filter(
                school_year_id=selected_year_id).order_by('Name')

    # 5. Aplicar filtro de Trimestre (si se seleccionó)
    if selected_trimester_id:
        try:
            selected_trimester_id = int(selected_trimester_id)
        except (ValueError, TypeError):
            selected_trimester_id = None

        if selected_trimester_id:
            grades_qs = grades_qs.filter(trimester_id=selected_trimester_id)
            ausencias_qs = ausencias_qs.filter(
                trimester_id=selected_trimester_id)

    # --- FIN DE LA LÓGICA DE FILTROS ---

    # Verifica si se pasó un 'course' ID como parámetro GET.
    return_course = request.GET.get('course')

    # Prepara el contexto para la plantilla.
    context = {
        "student": student,
        # Pasa el QuerySet filtrado
        "grades": grades_qs.order_by('trimester__Name'),
        # Pasa el QuerySet filtrado
        "ausencias": ausencias_qs.order_by('-date_time'),
        "is_tutor": False,
        "return_course": return_course,

        # --- Pasa el contexto de los filtros a la plantilla ---
        "all_school_years": all_school_years,
        "available_trimesters": available_trimesters,
        "selected_year_id": selected_year_id,
        "selected_trimester_id": selected_trimester_id,
    }
    # Renderiza el contenido del dashboard del estudiante.
    return render(request, "mainapp/student_dashboard_content.html", context)


@login_required
def tutor_dashboard(request):
    # Requiere autenticación.
    profile = request.user.profile

    # Restricción de rol: solo el 'tutor' puede acceder a esta vista.
    if profile.role != 'tutor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Obtiene la lista de "hijos" (estudiantes) asociados al perfil del tutor.
    children = profile.children.all()
    children_info = []

    # Itera sobre cada hijo para recopilar sus notas y ausencias.
    for child in children:
        grades = Grade.objects.filter(student=child)
        ausensias = Ausencias.objects.filter(
            student=child).order_by('-date_time')
        children_info.append({
            "student": child,
            "grades": grades,
            "ausensias": ausensias,
        })

    # Intenta obtener el índice del hijo seleccionado desde el parámetro GET 'child', por defecto 0.
    try:
        selected_child = int(request.GET.get("child", 0))
    except (TypeError, ValueError):
        selected_child = 0

    # Selecciona el objeto de información del hijo si el índice es válido.
    selected_child_obj = children_info[selected_child] if children_info and 0 <= selected_child < len(
        children_info) else None

    # Prepara el contexto con la información de todos los hijos y el hijo seleccionado.
    context = {
        "children_info": children_info,
        "selected_child": selected_child,
        "selected_child_obj": selected_child_obj,
        "is_tutor": True,  # Indica que esta es la vista de tutor.
    }
    # Reutiliza la plantilla 'student_file.html' para mostrar la información del hijo seleccionado.
    return render(request, "mainapp/student_file.html", context)


@login_required
def grades_csv(request, student_id=None):
    # Vista genérica para descargar calificaciones en formato CSV, adaptable por rol.
    profile = request.user.profile
    grades = Grade.objects.none()  # Inicializa un QuerySet vacío.
    filename = "student_data.csv"

    # --- OBTENER FILTROS DE LA URL ---
    selected_year_id = request.GET.get('school_year_id')
    selected_trimester_id = request.GET.get('trimester_id')

    if profile.role == "student" and profile.student:
        # Estudiante: filtra solo sus propias notas.
        student = profile.student
        grades = Grade.objects.filter(student=student)  # QuerySet base
        filename = f"{student.Name}_notas.csv"
    elif profile.role == "tutor":
        # Tutor: filtra las notas de todos sus hijos.
        children = list(profile.children.all())
        grades = Grade.objects.filter(student__in=children)  # QuerySet base
        filename = f"{request.user.username}_notas.csv"
    elif profile.role == "professor":
        if student_id:
            # Profesor (con ID de estudiante): filtra las notas de un estudiante específico.
            student = get_object_or_404(Students, pk=student_id)
            grades = Grade.objects.filter(student=student)  # QuerySet base
            filename = f"{student.Name}_notas.csv"
        else:
            # Profesor (sin ID): obtiene todas las notas de la base de datos.
            grades = Grade.objects.all()  # QuerySet base
            filename = "all_grades.csv"
    else:
        # Acceso denegado para cualquier otro rol.
        return render(request, 'forbidden.html', {"user": request.user, "profile": profile})

    # --- APLICAR FILTROS AL QUERYSET BASE ---
    if selected_year_id:
        try:
            grades = grades.filter(school_year_id=int(selected_year_id))
        except (ValueError, TypeError):
            pass  # Ignorar filtro inválido

    if selected_trimester_id:
        try:
            grades = grades.filter(trimester_id=int(selected_trimester_id))
            # Opcional: Modificar el nombre del archivo si se filtra por trimestre
            trim_obj = Trimester.objects.get(pk=int(selected_trimester_id))
            filename = filename.replace(".csv", f"_T{trim_obj.Name}.csv")
        except (ValueError, TypeError, Trimester.DoesNotExist):
            pass  # Ignorar filtro inválido

    # Configura la respuesta HTTP para la descarga CSV.
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    # Escribe el encabezado CSV.
    writer.writerow(
        ['Estudiante', 'Asignatura', 'Trimestre', 'Año Escolar', 'Nota', 'Tipo de Nota', 'Numero tipo de Nota', 'Comentario'])

    # Itera sobre las notas (ya filtradas) y escribe una fila por cada una.
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
    # Retorna la respuesta para iniciar la descarga.
    return response


@login_required
def class_grades_download(request, course_id):
    # Vista para descargar notas de una clase específica, con opciones de filtrado.
    profile = request.user.profile

    # Restricción de rol: solo el 'professor' puede acceder.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Recupera el objeto Course.
    course = get_object_or_404(Course, CourseID=course_id)

    # CORRECCIÓN APLICADA AQUÍ:
    # Se obtiene a los estudiantes filtrando por la relación Students -> Students_Courses -> Course.
    students_in_course = Students.objects.filter(
        students_courses__course_section=course).distinct()

    # Obtiene las asignaturas, trimestres, años escolares y tipos de nota que *existen* en las notas de estos estudiantes.
    subjects_in_course = Subjects.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('Name')
    trimesters = Trimester.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('Name')
    school_years = School_year.objects.filter(
        grade__student__in=students_in_course).distinct().order_by('year')
    grade_types = Grade.objects.filter(
        student__in=students_in_course).values_list('grade_type', flat=True).distinct().order_by('grade_type')

    # Maneja la petición POST para generar y descargar el CSV filtrado.
    if request.method == 'POST':
        # Obtiene los filtros seleccionados por el usuario.
        selected_subject_id = request.POST.get('subject')
        selected_trimester_id = request.POST.get('trimester')
        selected_school_year_id = request.POST.get('school_year')
        selected_grade_type = request.POST.get('grade_type')

        # Empieza con todas las notas de los estudiantes del curso.
        grades = Grade.objects.filter(student__in=students_in_course)

        # Aplica los filtros de forma incremental al QuerySet.
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
    Vista para crear una nota nueva o editar una existente.
    Incluye lógica para pre-seleccionar el estudiante y el año escolar más reciente.
    """
    # Asumo que Profile se relaciona con User.
    try:
        profile = request.user.profile
    except User.profile.RelatedObjectDoesNotExist:
        # Manejar el caso si el usuario no tiene perfil (ajusta esto según tu app)
        return render(request, "error.html", {"message": "Usuario sin perfil asociado."})

    # Restricción de rol: solo el 'professor' puede acceder.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student_instance = None
    grade_instance = None
    initial_data = {}

    # 1. Obtener el año escolar más reciente
    latest_year = School_year.objects.all().order_by('-year').first()

    # Determina si es edición o creación y obtiene las instancias necesarias.
    if grade_id:
        # Edición: recupera la nota y el estudiante asociado.
        grade_instance = get_object_or_404(Grade, id=grade_id)
        student_instance = grade_instance.student
        # Fija el estudiante actual en el initial_data para el campo oculto
        initial_data['student'] = student_instance.pk
    elif student_id:
        # Creación: recupera el estudiante al que se le va a asignar la nota.
        student_instance = get_object_or_404(Students, pk=student_id)
        # Fija el estudiante en el initial_data para el campo oculto
        initial_data['student'] = student_instance.pk

        # 2. FIJAR AÑO RECIENTE COMO VALOR INICIAL (Solo en creación)
        if latest_year:
            # Esto selecciona el año más reciente en el desplegable
            initial_data['school_year'] = latest_year.pk

    # Maneja la petición POST (envío del formulario de nota).
    if request.method == "POST":
        # Instancia el formulario, en modo edición si grade_instance existe.
        form = GradeForm(request.POST, instance=grade_instance)

        if form.is_valid():
            # El campo 'student' se guarda automáticamente porque su valor viene en request.POST (HiddenInput)
            g = form.save()

            messages.success(request, "Grade saved successfully.")
            # Redirecciona al dashboard del estudiante afectado.
            return redirect('student_dashboard_content', student_id=student_instance.pk)
    else:
        # Petición GET: Muestra el formulario, prellenado con initial_data
        form = GradeForm(instance=grade_instance, initial=initial_data)

    # Prepara el contexto para la plantilla del formulario.
    context = {
        "form": form,
        # Booleano para indicar si es modo edición.
        "is_edit": grade_instance is not None,
        "student": student_instance,
    }
    # Renderiza el formulario de creación/edición de nota.
    return render(request, "mainapp/grade_form.html", context)


# =================================================================
# 2. VISTA AJAX: CARGAR TRIMESTRES
# =================================================================

def load_trimesters(request):
    """
    Retorna los trimestres de un año escolar dado como JSON para la llamada AJAX.
    """
    school_year_id = request.GET.get('school_year_id')

    trimesters = Trimester.objects.filter(
        school_year_id=school_year_id).order_by('Name')

    trimester_list = [
        # CAMBIO CLAVE: Usamos trimester.Name (el valor entero 1, 2, 3) como el texto a mostrar
        {'id': trimester.pk,
         'name': trimester.Name  # <-- Ahora devuelve 1, 2, o 3
         }
        for trimester in trimesters
    ]

    return JsonResponse({'trimesters': trimester_list})


@login_required
def create_edit_ausencia(request, ausencia_id=None, student_id=None):
    # Vista para crear una ausencia nueva o editar una existente (similar a create_edit_grade).
    profile = request.user.profile

    # Restricción de rol: solo el 'professor' puede acceder.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    student_instance = None
    ausencia_instance = None

    # Determina si es edición o creación.
    if ausencia_id:
        # Edición: recupera la ausencia y el estudiante asociado.
        ausencia_instance = get_object_or_404(Ausencias, id=ausencia_id)
        student_instance = ausencia_instance.student
    elif student_id:
        # Creación: recupera el estudiante para la nueva ausencia.
        student_instance = get_object_or_404(Students, pk=student_id)

    # Maneja la petición POST (envío del formulario de ausencia).
    if request.method == "POST":
        # Instancia el formulario de edición (AusenciaEditForm) o creación.
        form = AusenciaEditForm(request.POST, instance=ausencia_instance)

        if form.is_valid():
            # Obtiene la instancia del modelo sin guardar (commit=False).
            ausencia = form.save(commit=False)

        # Asigna el estudiante (solo si es una nueva ausencia).
        if not ausencia_id:
            ausencia.student = student_instance

        # Guarda la instancia final en la base de datos.
        ausencia.save()

        messages.success(request, "Absence saved successfully.")
        # Redirecciona al dashboard del estudiante.
        return redirect('student_dashboard_content', student_id=student_instance.pk)
    else:
        # Petición GET: Muestra el formulario.
        form = AusenciaEditForm(instance=ausencia_instance)

    # Prepara el contexto.
    context = {
        "form": form,
        # Booleano para indicar si es modo edición.
        "is_edit": ausencia_instance is not None,
        "student": student_instance,
    }
    # Renderiza el formulario de creación/edición de ausencia.
    return render(request, "mainapp/ausencia_form.html", context)


@login_required
def search_students(request):
    profile = request.user.profile

    # Restricción de rol: solo el 'professor' puede acceder a la búsqueda.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Obtiene el término de búsqueda ('q') y el filtro opcional de curso ('course') de los parámetros GET.
    query = (request.GET.get('q') or '').strip()
    # ID opcional para limitar la búsqueda a una clase.
    course_id = request.GET.get('course')

    students_qs = Students.objects.none()  # Inicializa un QuerySet vacío.

    # Lógica para aplicar el filtro de curso.
    if course_id:
        try:
            # Intenta obtener el objeto Course.
            course_obj = Course.objects.get(CourseID=course_id)
            # CORRECCIÓN: Filtra estudiantes por la relación correcta
            # Students -> Students_Courses -> Course
            students_qs = Students.objects.filter(
                students_courses__course_section=course_obj
            ).distinct()
        except Course.DoesNotExist:
            # Si el curso no existe, el QuerySet se mantiene vacío (no hay resultados).
            students_qs = Students.objects.none()
    else:
        # Si no hay filtro de curso, la búsqueda comienza con todos los estudiantes.
        students_qs = Students.objects.all()

    # Define una función local para normalizar cadenas, eliminando acentos (diacríticos).
    def _strip_accents(text):
        if not text:
            return ''
        # Normaliza la cadena a su forma canónica de descomposición (NFKD).
        nkfd = unicodedata.normalize('NFKD', text)
        # Filtra los caracteres combinatorios (los acentos).
        return ''.join([c for c in nkfd if not unicodedata.combining(c)])

    # Lógica para aplicar el filtro de la consulta ('query').
    if query:
        # Normaliza la consulta del usuario (sin acentos, minúsculas).
        qnorm = _strip_accents(query).lower()

        # Intento de optimización: usa `unaccent` a nivel de base de datos (típico de PostgreSQL).
        try:
            # Importaciones necesarias para operaciones avanzadas de base de datos.
            from django.db.models import Func, F

            # Construye un QuerySet candidato anotando campos sin acento para filtrar de forma eficiente.
            students_qs_candidate = students_qs.annotate(
                # Crea el campo 'name_unaccent'
                name_unaccent=Func(F('Name'), function='unaccent'),
                # Crea el campo 'email_unaccent'
                email_unaccent=Func(F('Email'), function='unaccent'),
            ).filter(
                # Filtra por coincidencia parcial (icontains) en el nombre O el email sin acentos.
                Q(name_unaccent__icontains=qnorm) | Q(
                    email_unaccent__icontains=qnorm)
            ).order_by('Name')

            # Intenta evaluar el QuerySet (tomando un elemento) para forzar la ejecución de SQL.
            # Esto detecta si la función `unaccent` existe en la base de datos.
            try:
                _ = list(students_qs_candidate[:1])
                # Si tiene éxito, usa el QuerySet optimizado.
                students_qs = students_qs_candidate
            except Exception:
                # Si el SQL falla (la función `unaccent` no está disponible), salta al bloque `except` principal.
                raise
        except Exception:
            # Mecanismo de respaldo (fallback) en caso de que la función `unaccent` no esté disponible.
            matched = []
            # Itera sobre el QuerySet original y filtra en Python (menos eficiente para grandes volúmenes).
            for s in students_qs:
                name_val = getattr(s, 'Name', '') or ''
                email_val = getattr(s, 'Email', '') or ''
                # Compara si la consulta normalizada está en el nombre o email normalizados del estudiante.
                if qnorm in _strip_accents(name_val).lower() or qnorm in _strip_accents(email_val).lower():
                    matched.append(s)
            # Usa la lista de estudiantes filtrados en Python.
            students_qs = matched
    else:
        # Si no hay consulta de búsqueda y tampoco filtro de curso, devuelve un QuerySet vacío.
        if not course_id:
            students_qs = Students.objects.none()

    # Prepara los resultados finales para la plantilla.
    results = []
    for s in students_qs:
        # CORRECCIÓN: Obtiene los cursos usando la relación correcta
        # Students -> Students_Courses -> Course
        student_courses_relations = Students_Courses.objects.filter(
            student=s
        ).select_related('course_section')

        # Extrae los objetos Course de las relaciones
        courses = [
            sc.course_section
            for sc in student_courses_relations
            if sc.course_section is not None
        ]

        # Formatea las etiquetas de los cursos (ej: "Eso 1B").
        course_labels = [f"{c.Tipo} {c.Section}" for c in courses]

        results.append({
            'student': s,
            # Lista de cursos a los que está inscrito el estudiante.
            'courses': course_labels,
        })

    # Prepara el contexto y renderiza la página de resultados.
    context = {
        'query': query,
        'results': results,
        'course_id': course_id,
    }
    return render(request, 'mainapp/search_results.html', context)


@login_required
def import_grades(request, course_id=None):
    profile = request.user.profile

    # Restricción de rol: solo el 'professor' puede acceder a la importación.
    if profile.role != 'professor':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Recupera el objeto Course si se proporciona el ID en la URL.
    course = None
    if course_id:
        course = get_object_or_404(Course, CourseID=course_id)

    # Maneja la petición POST (envío del archivo CSV).
    if request.method == 'POST':
        # Obtiene el archivo CSV subido.
        csv_file = request.FILES.get('csv_file')

        # Validación básica del archivo.
        if not csv_file:
            messages.error(request, 'Por favor selecciona un archivo CSV.')
            return render(request, 'mainapp/import_grades.html', {'course': course})

        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'El archivo debe ser un CSV.')
            return render(request, 'mainapp/import_grades.html', {'course': course})

        # Contadores de resultados de la importación.
        created_count = 0
        updated_count = 0
        error_count = 0
        errors = []

        try:
            # Lee el contenido del CSV, decodificándolo como UTF-8.
            csv_content = csv_file.read().decode('utf-8')
            # Usa DictReader para tratar cada fila como un diccionario (basado en el encabezado).
            reader = csv.DictReader(csv_content.splitlines())

            # Itera sobre las filas del CSV (el conteo comienza en 2 para incluir la fila de encabezado).
            for row_num, row in enumerate(reader, start=2):
                try:
                    # Intenta parsear los valores de la fila. Soporta nombres de columna en español o inglés como fallback.
                    student_name = row.get('Nombre_Estudiante') or row.get(
                        'student_name', '').strip()
                    subject_name = row.get('Asignatura') or row.get(
                        'subject_name', '').strip()
                    trimester_name = row.get('Trimestre') or row.get(
                        'trimester_name', '').strip()
                    school_year_str = row.get('Año_Escolar') or row.get(
                        'school_year', '').strip()
                    # Convierte la nota a float.
                    grade_value = float(row.get('Nota') or row.get('grade', 0))
                    grade_type = (row.get('Tipo_Nota') or row.get(
                        'grade_type', 'examen')).strip()
                    # Convierte el número de tipo de nota a entero.
                    grade_type_number = int(
                        row.get('Numero_Tipo_Nota') or row.get('grade_type_number', 0) or 0)
                    comments = (row.get('Comentarios')
                                or row.get('comments', '')).strip()

                    # Obtiene el estudiante y la asignatura (si no existen, lanza una excepción).
                    student = Students.objects.get(Name=student_name)
                    subject = Subjects.objects.get(Name=subject_name)

                    # Obtiene o crea el objeto School_year basado en el string.
                    school_year, _ = School_year.objects.get_or_create(
                        year=school_year_str,
                        defaults={'year': school_year_str}
                    )

                    # Obtiene o crea el objeto Trimester, asociado al año escolar.
                    trimester, _ = Trimester.objects.get_or_create(
                        Name=int(trimester_name),
                        school_year=school_year,
                        defaults={'Name': int(
                            trimester_name), 'school_year': school_year}
                    )

                    # Crea o actualiza la nota (Grade). Usa todos los campos como clave única.
                    grade, created = Grade.objects.update_or_create(
                        student=student,
                        subject=subject,
                        trimester=trimester,
                        school_year=school_year,
                        grade_type=grade_type,
                        grade_type_number=grade_type_number,
                        defaults={
                            # Estos son los campos que se actualizan o se establecen al crear.
                            'grade': grade_value,
                            'comments': comments,
                        }
                    )

                    # Incrementa los contadores.
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                # Manejo de errores específicos (si no encuentra estudiante o asignatura).
                except Students.DoesNotExist:
                    errors.append(
                        f"Fila {row_num}: Estudiante '{student_name}' no encontrado")
                    error_count += 1
                except Subjects.DoesNotExist:
                    errors.append(
                        f"Fila {row_num}: Asignatura '{subject_name}' no encontrada")
                    error_count += 1
                except Exception as e:
                    # Manejo de otros errores (ej: ValueError al convertir a float o int).
                    errors.append(f"Fila {row_num}: {str(e)}")
                    error_count += 1

            # Muestra el resumen de los resultados de la importación usando mensajes de Django.
            if created_count > 0:
                messages.success(request, f'✓ Creadas: {created_count} notas')
            if updated_count > 0:
                messages.info(
                    request, f'↻ Actualizadas: {updated_count} notas')
            if error_count > 0:
                messages.error(request, f'❌ Errores: {error_count} notas')
                # Muestra los primeros 10 errores para no saturar.
                for error in errors[:10]:
                    messages.error(request, error)
                if len(errors) > 10:
                    messages.error(
                        request, f'... y {len(errors) - 10} errores más')

        except Exception as e:
            # Captura errores al abrir o procesar el archivo CSV en general.
            messages.error(request, f'Error al procesar el archivo: {str(e)}')

    # Prepara el contexto para la plantilla (útil si hay que mostrar el curso).
    context = {
        'course': course,
    }
    # Renderiza la plantilla de importación de notas.
    return render(request, 'mainapp/import_grades.html', context)


@login_required
def adminage_dashboard_view(request):
    profile = request.user.profile
    # Restricción de rol: solo el 'administrator' puede ver este panel.
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Prepara el contexto con la lista de años escolares existentes, ordenados del más reciente al más antiguo.
    context = {
        'title': 'Panel de Administración Escolar',
        'school_years': School_year.objects.all().order_by('-year')
    }
    # Renderiza el dashboard principal.
    return render(request, "adminage/adminage_dashboard.html", context)


# =======================================================
# --- VISTA 1: CREAR AÑO ESCOLAR ---
# =======================================================
@login_required
def create_school_year_view(request):
    profile = request.user.profile
    # Restricción de rol.
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Maneja la creación del Año Escolar (POST).
    if request.method == 'POST':
        form = SchoolYearForm(request.POST)
        if form.is_valid():
            # 1. Crea y guarda el objeto School_year.
            school_year_obj = form.save()

            # 2. Crea automáticamente los 3 Trimestres asociados a ese año escolar.
            trimestres_a_crear = [
                Trimester(Name=1, school_year=school_year_obj),
                Trimester(Name=2, school_year=school_year_obj),
                Trimester(Name=3, school_year=school_year_obj),
            ]
            # Usa 'bulk_create' para insertar los 3 trimestres con una sola consulta a la base de datos (más eficiente).
            Trimester.objects.bulk_create(trimestres_a_crear)

            messages.success(
                request, f"Año Escolar {school_year_obj.year} creado. Se han generado 3 trimestres asociados.")

            # 3. Redirige a la siguiente vista de configuración (creación de cursos), pasando el ID del nuevo año escolar.
            url = reverse('create_courses_sections')
            return redirect(f'{url}?school_year_id={school_year_obj.pk}')
        else:
            # Si el formulario falla, muestra un error.
            messages.error(
                request, "Error al crear el Año Escolar. Por favor, corrija los errores.")
    else:
        # Petición GET: Muestra el formulario vacío.
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
    # Restricción de rol.
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Obtiene el ID del año escolar de los parámetros GET (debe venir de la vista anterior).
    school_year_id = request.GET.get('school_year_id')

    # Valida que el proceso haya comenzado con la definición de un Año Escolar.
    if not school_year_id:
        messages.error(request, "Debe comenzar definiendo un Año Escolar.")
        return redirect('adminage_dashboard')

    # Obtiene el objeto School_year (lanza 404 si no existe).
    try:
        school_year = School_year.objects.get(pk=school_year_id)
    except School_year.DoesNotExist:
        raise Http404("Año Escolar no encontrado.")

    context = {'school_year': school_year}

    # --- PASO 1 POST (Selección del Tipo: ESO, Bachillerato, etc.) ---
    if request.method == 'POST' and request.POST.get('step') == 'select_type':

        # Crea el formulario principal con los datos POST y el ID del año.
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

        # Si es válido, extrae el tipo de curso seleccionado.
        course_tipo = form_main.cleaned_data['course_tipo']

        # Llama a la función auxiliar para pasar al Paso 2.
        return _render_step2(request, course_tipo, school_year, form_main)

    # --- PASO 2 POST (Guardado de Secciones Confirmadas) ---
    elif request.method == 'POST' and request.POST.get('step') == 'confirm_sections':

        course_tipo = request.POST.get('course_tipo')

        # Vuelve a validar el formulario principal (contiene datos clave como el año y el tipo).
        form_main = CourseCreationForm(
            request.POST,
            initial_school_year_id=school_year_id,
            course_type_initial=course_tipo
        )

        if not form_main.is_valid():
            messages.error(request, "Error de validación. Vuelva a empezar.")
            return redirect('adminage_dashboard')

        # Crea una factoría para el formset (colección de formularios) para las secciones.
        CourseFormSet = formset_factory(CourseSectionForm, extra=0)
        # Instancia el formset con los datos POST.
        formset = CourseFormSet(request.POST)

        if formset.is_valid():
            num_created = 0

            # Itera sobre los formularios del formset para crear las secciones.
            for form_section in formset:
                if form_section.cleaned_data:
                    # Ej: '1'
                    main_course_name = form_section.cleaned_data['main_course_name']
                    # Ej: 3 (para A, B, C)
                    num_subsections = form_section.cleaned_data['num_subsections']
                    # Genera las letras de sección (A=65, B=66, etc.).
                    subsection_letters = [chr(65 + i)
                                          for i in range(num_subsections)]

                    new_courses = []
                    # Crea un objeto Course para cada subsección (Ej: '1A', '1B', '1C').
                    for letter in subsection_letters:
                        new_courses.append(
                            Course(
                                Tipo=course_tipo,
                                Section=f"{main_course_name}{letter}",
                                school_year=school_year
                            )
                        )
                    # Inserta todas las nuevas secciones de curso con 'bulk_create'.
                    Course.objects.bulk_create(new_courses)
                    num_created += len(new_courses)

            messages.success(
                request, f"{num_created} secciones de cursos ({course_tipo}) creadas exitosamente para {school_year}.")

            # Redirige al dashboard de administración.
            return redirect('adminage_dashboard')

        else:
            # Si el formset falla, muestra error y vuelve a renderizar el Paso 2 con los errores.
            messages.error(
                request, "Por favor, corrige los errores en las secciones.")
            return _render_step2(request, course_tipo, school_year, form_main, formset=formset)

    # --- PASO 1 GET (Carga Inicial) ---
    else:
        # Petición GET: Carga el formulario inicial de selección de Tipo.
        form = CourseCreationForm(
            initial_school_year_id=school_year_id,
            initial={'school_year': school_year}
        )
        context['form'] = form
        # Renderiza el primer paso.
        return render(request, "adminage/create_courses_step1.html", context)


# Función auxiliar para renderizar el Paso 2
def _render_step2(request, course_tipo, school_year, form_main, formset=None):
    profile = request.user.profile
    # Aplica la restricción de rol (por si se llama directamente con un rol incorrecto).
    if profile.role != 'administrator':
        return render(request, "forbidden.html", {"user": request.user, "profile": profile})

    # Si no se proporciona un formset (es la primera vez que se carga el Paso 2), se inicializa.
    if not formset:
        CourseFormSet = formset_factory(CourseSectionForm, extra=0)
        initial_data = []
        # Utiliza un diccionario global (MAIN_COURSES) para obtener los niveles principales del Tipo de curso.
        if course_tipo in MAIN_COURSES:
            for main_course_num in MAIN_COURSES[course_tipo]:
                # Prepara los datos iniciales para el formset.
                initial_data.append({
                    'main_course_name': str(main_course_num),
                    'display_name': f"{main_course_num}º {course_tipo}"
                })
        formset = CourseFormSet(initial=initial_data)

    context = {
        'form_main': form_main,  # Formulario principal (oculto)
        'formset': formset,  # Formset de secciones a definir
        'course_tipo': course_tipo,
        'school_year': school_year,
        'title': f"Definir Secciones para {course_tipo} ({school_year})"
    }
    # Renderiza la plantilla del Paso 2.
    return render(request, "adminage/create_courses_step2.html", context)


@login_required
def assign_subjects_view(request):
    """
    Vista compleja para asignar una asignatura (y profesor) a múltiples trimestres de un curso,
    definiendo opcionalmente un subconjunto de estudiantes del curso que tomarán esa materia.
    """
    # Obtiene las opciones de tipos de curso.
    course_types = Course.COURSE_TYPE_CHOICES

    # 1. DEFINICIÓN E INICIALIZACIÓN DE VARIABLES
    # Obtiene el año escolar más reciente como valor por defecto.
    latest_school_year = School_year.objects.order_by(
        '-year').only('pk').first()
    school_year_id = request.GET.get('school_year_id') or (
        str(latest_school_year.pk) if latest_school_year else '')

    # Formulario de asignación de asignatura/profesor.
    current_form = SubjectAssignmentForm()
    selected_course_id = request.GET.get('course_id')
    current_school_year = None
    trimesters = []
    course_students_links = None
    target_course = None

    # Si hay un ID de año escolar válido, carga los objetos necesarios.
    if school_year_id:
        try:
            current_school_year = School_year.objects.get(pk=school_year_id)
            # Filtra los trimestres asociados a ese año.
            trimesters = Trimester.objects.filter(
                school_year=current_school_year).order_by('Name')

            # Ajusta los QuerySets del formulario para Asignatura y Profesor.
            current_form.fields['subject'].queryset = Subjects.objects.all().order_by(
                'Name')
            current_form.fields['teacher'].queryset = Teachers.objects.all().order_by(
                'Name')

        except School_year.DoesNotExist:
            messages.error(request, "Año escolar no válido.")
            return redirect('assign_subjects')

    # Obtiene los enlaces de estudiantes (registros Students_Courses) si se selecciona un curso.
    if selected_course_id:
        try:
            target_course = Course.objects.get(pk=selected_course_id)
            # Obtiene los registros de la tabla intermedia que vinculan estudiantes al curso.
            course_students_links = Students_Courses.objects.filter(
                course_section=target_course
            ).select_related('student').order_by('student__Name')
        except Course.DoesNotExist:
            messages.warning(
                request, "El ID de curso seleccionado no es válido.")
            selected_course_id = None

    # 2. Manejo del POST (Creación/Actualización de Asignación Subjects_Courses)
    if request.method == 'POST':

        # Recupera IDs del POST (pueden venir de campos ocultos o filtros).
        selected_course_id = request.POST.get('course_id')
        school_year_id_post = request.POST.get('school_year_id')
        final_school_year_id = school_year_id_post or school_year_id

        # Valida IDs esenciales.
        if not selected_course_id or not final_school_year_id:
            messages.error(
                request, "Error: Debe seleccionar una sección de curso y un año escolar válido.")
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}')

        # Vuelve a obtener el objeto Course.
        try:
            target_course = Course.objects.get(pk=selected_course_id)
        except Course.DoesNotExist:
            messages.error(request, "Sección de curso no válida.")
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}')

        # Carga y valida el formulario de Asignatura/Profesor.
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
                # Si falla, se reestablecen los filtros para que el usuario pueda corregir.
                current_form = form
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            try:
                # Obtiene los objetos Trimester seleccionados.
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
            # Obtiene los IDs de los registros Students_Courses seleccionados (los *enlaces* a los estudiantes).
            assigned_students_courses_ids_selected = request.POST.getlist(
                'student_links_selected')

            try:
                # Convierte a una lista de IDs de enteros.
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

            # 1. Crea/Obtiene el registro Subjects_Courses para cada TRIMESTRE.
            for trimester in selected_trimesters:
                assignment, created = Subjects_Courses.objects.get_or_create(
                    subject=subject,
                    course=target_course,
                    trimester=trimester,
                    # Si se crea, asigna el profesor.
                    defaults={'teacher': teacher}
                )
                # Si ya existía, actualiza el profesor si es diferente.
                if not created and assignment.teacher != teacher:
                    assignment.teacher = teacher
                    assignment.save()

                newly_created_objects.append(assignment)

            # 2. Asigna los enlaces de estudiantes a CADA registro Subjects_Courses.
            if student_count > 0:
                for assignment in newly_created_objects:
                    # Usa .set() para reemplazar la lista de estudiantes asignados a esta materia-trimestre.
                    assignment.assigned_course_sections.set(
                        assigned_students_courses_ids)

                messages.success(
                    request, f"Asignación de {subject.Name} creada/actualizada para {len(newly_created_objects)} trimestre(s) y {student_count} estudiantes.")

            else:
                # Si no se seleccionó ningún estudiante, limpia la relación ManyToMany.
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
# --- VISTA ÚNICA: CREAR ESTUDIANTE Y ASIGNAR CLASE ---
# =======================================================
@login_required
def create_and_assign_student_view(request):
    """
    Gestiona la creación de un nuevo estudiante (tabla Students) y su asignación
    a una sección de curso específica (creando un registro en Students_Courses).
    """

    course_types = Course.COURSE_TYPE_CHOICES

    # Obtiene el ID del año escolar más reciente para preselección en los filtros AJAX.
    latest_school_year = School_year.objects.order_by(
        '-year').only('pk').first()
    latest_school_year_id = str(
        latest_school_year.pk) if latest_school_year else ''

    current_form = StudentCreationForm()

    if request.method == 'POST':
        current_form = StudentCreationForm(request.POST)
        # ID de la sección de curso seleccionada.
        course_id = request.POST.get('course_id')

        if current_form.is_valid():

            if not course_id:
                messages.error(
                    request, "Error: Debe seleccionar una sección de curso (Tipo, Nivel, Sección) para asignar al estudiante.")
            else:
                try:
                    # Obtiene el objeto Course seleccionado.
                    target_course = Course.objects.get(pk=course_id)
                except Course.DoesNotExist:
                    messages.error(
                        request, "Error: La sección de curso seleccionada no es válida.")
                else:
                    # 1. CREAR ESTUDIANTE (Tabla Students)
                    new_student = current_form.save()

                    # 2. CREAR LA RELACIÓN (Tabla Students_Courses)
                    # Esto vincula al estudiante recién creado con el curso.
                    Students_Courses.objects.create(
                        student=new_student,
                        course_section=target_course
                    )

                    messages.success(
                        request, f"Estudiante '{new_student.Name}' creado y asignado a {target_course.Section} exitosamente.")

                    # Redirige para mostrar el formulario limpio tras el éxito.
                    return redirect('create_and_assign_student')

        else:
            messages.error(
                request, "Error en los datos del estudiante. Revise el Nombre y Email.")

    context = {
        'title': 'Crear Estudiante y Asignar Clase',
        'form': current_form,
        'course_types': course_types,
        'current_school_year_id': latest_school_year_id,
        # Pasa el ID del curso seleccionado (en caso de que el POST haya fallado).
        'selected_course_id': request.POST.get('course_id', ''),
    }
    return render(request, "adminage/create_and_assign_student.html", context)


def reassign_students(request):
    """
    Vista principal para reasignar estudiantes de una clase a otra
    """
    if request.method == 'POST':
        # Procesar la reasignación
        # Lista de "student_id:course_id"
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

                # Buscar el registro existente del estudiante
                existing_assignment = Students_Courses.objects.filter(
                    student=student
                ).first()

                if existing_assignment:
                    # Actualizar la asignación existente
                    existing_assignment.course_section = new_course
                    existing_assignment.save()
                else:
                    # Crear nueva asignación si no existe
                    Students_Courses.objects.create(
                        student=student,
                        course_section=new_course
                    )

                success_count += 1

            except Exception as e:
                error_count += 1
                messages.error(
                    request, f"Error al reasignar estudiante: {str(e)}")

        if success_count > 0:
            messages.success(
                request, f"{success_count} estudiante(s) reasignado(s) correctamente.")
        if error_count > 0:
            messages.warning(
                request, f"Hubo {error_count} error(es) durante la reasignación.")

        return redirect('reassign_students')

    # GET: Mostrar el formulario
    school_years = School_year.objects.all().order_by('-year')
    course_types = Course.COURSE_TYPE_CHOICES

    context = {
        'school_years': school_years,
        'course_types': course_types,
    }

    return render(request, 'reassign_students.html', context)


def ajax_get_course_numbers(request):
    """
    Endpoint AJAX para obtener los números de curso disponibles
    según el año escolar y tipo seleccionados
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')

    print(f"DEBUG - ajax_get_course_numbers called:")
    print(f"  school_year_id: {school_year_id}")
    print(f"  course_type: {course_type}")

    if not school_year_id or not course_type:
        return JsonResponse({'numbers': [], 'error': 'Faltan parámetros'})

    try:
        # Obtener todos los cursos que coinciden
        courses = Course.objects.filter(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type
        )

        print(f"  Cursos encontrados: {courses.count()}")
        for course in courses:
            print(
                f"    - CourseID: {course.CourseID}, Tipo: {course.Tipo}, Section: {course.Section}, SchoolYear: {course.school_year.year}")

        # Extraer números únicos del campo Section
        sections = courses.values_list('Section', flat=True)

        # Intentar extraer el primer carácter numérico
        numbers = set()
        for section in sections:
            if section:
                # Extraer todos los dígitos del inicio de la cadena
                num = ''
                for char in section:
                    if char.isdigit():
                        num += char
                    else:
                        break
                if num:
                    numbers.add(num)

        numbers_list = sorted(list(numbers))
        print(f"  Números extraídos: {numbers_list}")

        return JsonResponse({'numbers': numbers_list})

    except Exception as e:
        print(f"  ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'numbers': [], 'error': str(e)})


def ajax_get_course_sections(request):
    """
    Endpoint AJAX para obtener las secciones (letras) disponibles
    según el año, tipo y número de curso
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    course_number = request.GET.get('course_number')

    print(f"DEBUG - ajax_get_course_sections called:")
    print(f"  school_year_id: {school_year_id}")
    print(f"  course_type: {course_type}")
    print(f"  course_number: {course_number}")

    if not all([school_year_id, course_type, course_number]):
        return JsonResponse({'sections': [], 'error': 'Faltan parámetros'})

    try:
        # Buscar secciones que empiecen con el número seleccionado
        courses = Course.objects.filter(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type,
            Section__startswith=course_number
        )

        print(f"  Cursos encontrados: {courses.count()}")

        # Extraer las letras (todo lo que viene después del número)
        sections = set()
        for course in courses:
            section = course.Section
            print(f"    - Procesando: {section}")
            if section:
                # Saltar los dígitos iniciales y tomar el resto
                letter_part = ''
                skip_digits = True
                for char in section:
                    if skip_digits and char.isdigit():
                        continue
                    skip_digits = False
                    letter_part += char

                if letter_part:
                    sections.add(letter_part)
                    print(f"      Letra extraída: {letter_part}")

        sections_list = sorted(list(sections))
        print(f"  Secciones finales: {sections_list}")

        return JsonResponse({'sections': sections_list})

    except Exception as e:
        print(f"  ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'sections': [], 'error': str(e)})


def ajax_get_students(request):
    """
    Endpoint AJAX para obtener estudiantes de una clase específica
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    course_number = request.GET.get('course_number')
    section_letter = request.GET.get('section_letter')

    print(f"DEBUG - ajax_get_students called:")
    print(f"  school_year_id: {school_year_id}")
    print(f"  course_type: {course_type}")
    print(f"  course_number: {course_number}")
    print(f"  section_letter: {section_letter}")

    if not all([school_year_id, course_type, course_number, section_letter]):
        return JsonResponse({'students': [], 'error': 'Faltan parámetros'})

    # Construir el Section completo (ej: '1A')
    section_full = f"{course_number}{section_letter}"
    print(f"  Buscando curso con Section: {section_full}")

    # Buscar el curso específico
    try:
        course = Course.objects.get(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type,
            Section=section_full
        )

        print(f"  Curso encontrado: {course} (ID: {course.CourseID})")

        # Obtener estudiantes asignados a este curso
        student_courses = Students_Courses.objects.filter(
            course_section=course
        ).select_related('student')

        print(f"  Estudiantes encontrados: {student_courses.count()}")

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
        print(f"  ERROR: Curso no encontrado")
        return JsonResponse({'students': [], 'error': 'Curso no encontrado'})
    except Exception as e:
        print(f"  ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'students': [], 'error': str(e)})


def ajax_get_destination_courses(request):
    """
    Endpoint AJAX para obtener el ID del curso destino
    """
    school_year_id = request.GET.get('school_year_id')
    course_type = request.GET.get('course_type')
    course_number = request.GET.get('course_number')
    section_letter = request.GET.get('section_letter')

    if not all([school_year_id, course_type, course_number, section_letter]):
        return JsonResponse({'course_id': None, 'error': 'Faltan parámetros'})

    section_full = f"{course_number}{section_letter}"

    try:
        course = Course.objects.get(
            school_year__SchoolYearID=school_year_id,
            Tipo=course_type,
            Section=section_full
        )
        return JsonResponse({'course_id': course.CourseID})
    except Course.DoesNotExist:
        return JsonResponse({'course_id': None, 'error': 'Curso no encontrado'})
    except Exception as e:
        return JsonResponse({'course_id': None, 'error': str(e)})
