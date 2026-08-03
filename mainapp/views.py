import codecs
import csv
import logging
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.forms import formset_factory
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django_ratelimit.decorators import ratelimit

from .forms import (
    AusenciaEditForm, AusenciaForm, CourseCreationForm, CourseSectionForm,
    GradeForm, MAIN_COURSES, SchoolYearForm, StudentCreationForm,
    SubjectAssignmentForm,
)
from .models import (
    Ausencias, Course, Grade, Profile, School_year, Students,
    Students_Courses, Subjects, Subjects_Courses, Teachers, Trimester,
)

# CSV import bounds. Django caps neither: DATA_UPLOAD_MAX_MEMORY_SIZE excludes
# uploaded files and FILE_UPLOAD_MAX_MEMORY_SIZE is only a spool threshold.
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000


audit_log = logging.getLogger('mainapp.audit')


def audit(request, action, **fields):
    """Record a security-relevant action: who, what, from where, why.

    Callers pass identifiers, never payloads or credentials: log lines are
    long-lived and student data is a liability. The timestamp comes from the
    formatter.
    """
    user = getattr(request, 'user', None)
    audit_log.info(
        'action=%s user=%s ip=%s path=%s %s',
        action,
        getattr(user, 'username', None) or 'anonymous',
        request.META.get('REMOTE_ADDR', '-'),
        request.path,
        ' '.join(f'{k}={v}' for k, v in fields.items()),
    )


def _cell(row, *names):
    """First non-empty value among `names`, stripped.

    Exists because `row.get('A') or row.get('b', '').strip()` binds `.strip()`
    to the fallback only, so a padded value in the primary column was never
    stripped and silently failed to match.
    """
    for name in names:
        value = row.get(name)
        if value:
            return value.strip()
    return ''


def role_required(*roles):
    """Require login, a Profile, and one of `roles`. Denials return 403."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            # RelatedObjectDoesNotExist subclasses AttributeError, so getattr
            # yields None when the user has no Profile row (they are created
            # by hand in Django admin, so that is a real case).
            profile = getattr(request.user, 'profile', None)
            if profile is None:
                logout(request)
                return redirect('login')
            if profile.role not in roles:
                return render(request, "forbidden.html",
                              {"user": request.user, "profile": profile},
                              status=403)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def teacher_required(view):
    """`role_required('professor')` plus a guaranteed Teachers link.

    Wrapped views can rely on `request.user.profile.teacher` being set. A
    professor account with no link reaches no student data at all: the
    scoping fails closed, which is the point of it.
    """
    @role_required('professor')
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.profile.teacher is None:
            return render(request, "forbidden.html",
                          {"user": request.user,
                           "profile": request.user.profile,
                           "unlinked_teacher": True}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


def teacher_courses(teacher):
    """Course sections this teacher is assigned to teach in."""
    return Course.objects.filter(subjects_courses__teacher=teacher).distinct()


def teacher_students(teacher):
    """Students enrolled in any course section this teacher teaches.

    Scoped through Subjects_Courses.course rather than through
    `assigned_course_sections`: that M2M is optional in the only UI that
    populates it and is actively cleared on an empty submit, so it is
    routinely empty and cannot be relied on for authorization.
    """
    return Students.objects.filter(
        students_courses__course_section__subjects_courses__teacher=teacher
    ).distinct()


def student_initials(name):
    """Up to two initials, for the 2px left rule that stands in for an avatar.

    Direction C has no icon tiles, so this glyph is the only per-student mark
    on a row and every list that shows one must derive it the same way. Names
    here run to five words ("Christian Francisco Gonzalez Di Antonio"), so it
    takes the first two rather than all of them.
    """
    parts = [p for p in (name or '').split() if p]
    return ''.join(p[0] for p in parts[:2]).upper()


class ClassScope:
    """What a class page is currently looking at: year, trimester, subject, roster.

    Every class-level readout is a claim about a scope, so the scope is
    resolved once, in one place, instead of each view re-deriving it.

    The school year is NOT a query parameter here: `Course.school_year` fixes
    it. A `?school_year_id=` arriving from a bookmark is deliberately ignored
    rather than honoured — a year that disagrees with the course would describe
    a class that does not exist. The in-app links that used to append it
    (`section_courses.html`) no longer do; the only params worth propagating
    into this page are the two `query_params` emits.

    `roster_source` says which of the two roster rules produced `students`,
    because the two mean different things to a teacher and must be labelled
    differently in the UI:

    * ``'course'``  — everyone enrolled in the course section. The default, and
      the fallback whenever a subject has no roster of its own.
    * ``'subject'`` — the subset in `Subjects_Courses.assigned_course_sections`,
      used only when a subject is selected *and* that M2M is non-empty.

    The hybrid exists because pure subject-scoping has no defined answer before
    a subject is picked, and because that M2M is populated by exactly one admin
    form which clears it on an empty submit (`views.py`, `assign_subjects_view`)
    — so it is frequently empty and cannot stand alone. It is still the only
    thing in the schema that models optativas, and this is its first reader.
    """

    def __init__(self, course, school_year, trimesters, trimester,
                 subjects_courses, subject_courses, students, roster_source):
        self.course = course
        self.school_year = school_year
        self.trimesters = trimesters
        self.trimester = trimester
        self.subjects_courses = subjects_courses
        self.subject_courses = subject_courses
        self.students = students
        self.roster_source = roster_source

    @property
    def subject(self):
        """The selected Subject, or None when the page is showing all of them."""
        return self.subject_courses.subject if self.subject_courses else None

    @property
    def query_params(self):
        """The scope as URL params, so redirects and links keep the page in place."""
        params = {}
        if self.trimester:
            params['trimester_id'] = self.trimester.pk
        if self.subject_courses:
            params['subject_courses_id'] = self.subject_courses.pk
        return params

    @property
    def query_string(self):
        params = self.query_params
        return urlencode(params) if params else ''


def resolve_class_scope(course, trimester_id=None, subject_courses_id=None):
    """Build the ClassScope for `course` from raw (untrusted) request params.

    Unrecognised ids fall back to the default scope instead of raising: these
    arrive from bookmarks and hand-edited URLs, and a 404 on a stale trimester
    link would be a worse page than the current trimester.
    """
    school_year = course.school_year

    trimesters = list(
        Trimester.objects.filter(school_year=school_year).order_by('Name')
    )
    trimester = None
    if trimester_id:
        trimester = next(
            (t for t in trimesters if str(t.pk) == str(trimester_id)), None)
    if trimester is None:
        trimester = trimesters[0] if trimesters else None

    # Subjects_Courses carries a trimester FK, so the subject set is per
    # trimester, not per course. Without this filter the live DB prints
    # "Matematicas" three times.
    subjects_courses = list(
        Subjects_Courses.objects
        .filter(course=course, trimester=trimester)
        .select_related('subject', 'teacher')
        .order_by('subject__Name')
    ) if trimester else []

    subject_courses = None
    if subject_courses_id:
        subject_courses = next(
            (sc for sc in subjects_courses
             if str(sc.pk) == str(subject_courses_id)), None)

    students = Students.objects.filter(students_courses__course_section=course)
    roster_source = 'course'

    if subject_courses is not None:
        # Filter the M2M back down to this course. Nothing constrains an
        # enrolment in `assigned_course_sections` to belong to the course that
        # owns the Subjects_Courses row, and a roster must never widen past the
        # class the teacher opened.
        assigned = subject_courses.assigned_course_sections.filter(
            course_section=course)
        if assigned.exists():
            students = Students.objects.filter(students_courses__in=assigned)
            roster_source = 'subject'

    return ClassScope(
        course=course,
        school_year=school_year,
        trimesters=trimesters,
        trimester=trimester,
        subjects_courses=subjects_courses,
        subject_courses=subject_courses,
        students=students.distinct().order_by('Name'),
        roster_source=roster_source,
    )


class StudentMetrics:
    """One student's figures within a ClassScope.

    `mean` is None, never 0, when the student has no grades: zero is a mark and
    "not yet graded" is not one. The template must render the two differently.
    """

    def __init__(self, student, grade_count, grade_total, ausencias_count):
        self.student = student
        self.grade_count = grade_count
        self.grade_total = grade_total
        self.ausencias_count = ausencias_count

    @property
    def evaluated(self):
        return self.grade_count > 0

    @property
    def mean(self):
        if not self.grade_count:
            return None
        return _round2(self.grade_total / self.grade_count)

    @property
    def failing(self):
        """Below 5. Not a schema rule — the app's own convention, already
        applied as `grade-fail` in student_dashboard_content.html."""
        return self.mean is not None and self.mean < 5

    @property
    def initials(self):
        """Up to two initials, for the register's left rule."""
        return student_initials(self.student.Name)


class ClassMetrics:
    """Aggregates for a class page: per student, plus the strip along the top.

    Read the labels off this class carefully, because two of them are easy to
    state wrongly:

    * `grade_count` has **no denominator**. `Grade` is unique on
      (student, subject, trimester, school_year, grade_type, grade_type_number),
      which permits unbounded grades per student per subject per trimester.
      Any "N de M" readout would be a fabrication.
    * `mean` averages **every grade type in scope** — `examen`, `parcial`,
      `trimestral`, `final`, `otros` alike. Known and accepted flaw: a
      `trimestral` row is counted alongside the `examen` rows it summarises.
      The label must therefore name its pool ("media de las notas
      registradas"), not imply a syllabus-weighted average. A `grade_type`
      filter is a later page.
    """

    def __init__(self, scope, rows):
        self.scope = scope
        self.rows = rows

    @property
    def enrolled(self):
        return len(self.rows)

    @property
    def evaluated(self):
        return sum(1 for r in self.rows if r.evaluated)

    @property
    def grade_count(self):
        return sum(r.grade_count for r in self.rows)

    @property
    def ausencias_count(self):
        return sum(r.ausencias_count for r in self.rows)

    @property
    def mean(self):
        """Class mean, weighted by each student's grade count.

        Deliberately not the mean of the per-student means: those are a
        different number whenever students hold different numbers of grades,
        which is the normal case here. Averaging averages would let a student
        with one grade weigh as much as a student with ten.
        """
        count = self.grade_count
        if not count:
            return None
        total = sum((r.grade_total for r in self.rows), Decimal('0'))
        return _round2(total / count)


def _round2(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def class_metrics(scope):
    """Grade and absence figures for every student in `scope`.

    Two aggregate queries plus a Python merge, never one annotated queryset
    over Students: `grade` and `ausencias` are both multi-valued relations, so
    annotating both at once multiplies the rows and inflates every count. The
    merge also keeps the query count flat in the size of the roster.

    Scoping a grade to a class is unenforced by the schema — `Grade` has no FK
    to `Course` (`models.py`), so the join runs through roster ∩ subject set ∩
    trimester ∩ school year. Consequence worth knowing: a student enrolled in
    two courses in the same year contributes their grades to both class pages.
    """
    students = list(scope.students)

    if scope.subject_courses is not None:
        subject_ids = [scope.subject_courses.subject_id]
    else:
        # No subject selected: every subject taught to this course in this
        # trimester. Not "every grade the student holds" — that would pull in
        # subjects belonging to other courses entirely.
        subject_ids = [sc.subject_id for sc in scope.subjects_courses]

    grades = {}
    absences = {}
    if students and subject_ids and scope.trimester:
        base = dict(student__in=students, subject_id__in=subject_ids,
                    trimester=scope.trimester, school_year=scope.school_year)

        grades = {
            row['student']: (row['n'], row['total'])
            for row in (Grade.objects.filter(**base)
                        .values('student')
                        .annotate(n=Count('pk'), total=Sum('grade')))
        }
        absences = {
            row['student']: row['n']
            for row in (Ausencias.objects.filter(**base)
                        .values('student')
                        .annotate(n=Count('pk')))
        }

    rows = []
    for student in students:
        count, total = grades.get(student.pk, (0, Decimal('0')))
        rows.append(StudentMetrics(
            student=student,
            grade_count=count,
            grade_total=total or Decimal('0'),
            ausencias_count=absences.get(student.pk, 0),
        ))

    return ClassMetrics(scope=scope, rows=rows)


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
            return render(request, "mainapp/login.html",
                          {"error": "Introduce el usuario y la contraseña."})

        # No existence pre-check: distinguishing "no such user" from "wrong
        # password" is a user-enumeration oracle, and skipping the password
        # hash on the unknown-user path is a timing oracle. authenticate()
        # already returns None for both, in constant time.
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # Login successful.
            login(request, user)
            profile = request.user.profile
            audit(request, 'login.success', role=profile.role)

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
            # Invalid credentials. Logged without the attempted username:
            # failed-login volume is the signal, not who was targeted.
            audit(request, 'login.failure', reason='invalid_credentials')
            # One message for both "no such user" and "wrong password" — the
            # same reason the view does not pre-check existence above.
            return render(request, "mainapp/login.html",
                          {"error": "Usuario o contraseña incorrectos."})

    return render(request, "mainapp/login.html")


def logoutUser(request):
    # Logs out user.
    audit(request, 'logout')
    logout(request)
    return redirect('login')


@role_required('tutor', 'legal_tutor', 'student')
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

    # FILTERS: School Year & Trimester
    all_school_years = list(School_year.objects.all().order_by('-year'))

    # Get filter params.
    selected_year_id_raw = request.GET.get('school_year_id')
    selected_trimester_id_raw = request.GET.get('trimester_id')

    # Convert to int. The matched object is kept alongside the id: the page
    # names the active year and trimester in its heading, and re-deriving that
    # in the template means a loop over a list this scan already walked.
    selected_year_id = None
    selected_year = None
    if selected_year_id_raw:
        try:
            candidate = int(selected_year_id_raw)
        except (ValueError, TypeError):
            candidate = None
        if candidate is not None:
            match = next(
                (s for s in all_school_years if s.SchoolYearID == candidate), None)
            if match is not None:
                selected_year_id = candidate
                selected_year = match

    # Get available trimesters.
    available_trimesters = []
    selected_trimester_id = None
    selected_trimester = None
    if selected_year_id:
        available_trimesters = Trimester.objects.filter(
            school_year__SchoolYearID=selected_year_id
        ).order_by('Name')
        if selected_trimester_id_raw:
            try:
                t_candidate = int(selected_trimester_id_raw)
            except (ValueError, TypeError):
                t_candidate = None
            if t_candidate is not None:
                t_match = next(
                    (t for t in available_trimesters if t.TrimesterID == t_candidate), None)
                if t_match is not None:
                    selected_trimester_id = t_candidate
                    selected_trimester = t_match

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
                # Same glyph the class register uses, so a person is marked the
                # same way on every list. Display only.
                'initials': student_initials(child.Name),
                'grades': child_grades.select_related('subject', 'trimester', 'school_year'),
                'ausencias': child_ausencias.select_related('subject', 'trimester', 'school_year'),
            })

        selected_child_obj = children_info[selected_child] if children_info else None

    # PREPARE CONTEXT
    context = {
        "student": student,
        "initials": student_initials(student.Name) if student else '',
        "grades": grades,
        "ausencias": ausencias,
        "is_tutor": is_tutor,
        "children_info": children_info if is_tutor else None,
        "selected_child": selected_child if is_tutor else None,
        "selected_child_obj": selected_child_obj if is_tutor else None,
        # Filter variables. The objects accompany the ids so the heading can
        # name the active scope without re-deriving it in the template.
        "all_school_years": all_school_years,
        "available_trimesters": available_trimesters,
        "selected_year_id": selected_year_id,
        "selected_trimester_id": selected_trimester_id,
        "selected_year": selected_year,
        "selected_trimester": selected_trimester,
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


@teacher_required
def teacher_dashboard(request):
    # Get user profile.
    profile = request.user.profile

    # Courses scoped to this teacher. teacher_students() is not called here:
    # the page lists classes, and the student/grade/absence querysets that
    # used to be built alongside were never rendered by the template.
    my_courses = teacher_courses(profile.teacher)

    # Get available school years.
    all_school_years = School_year.objects.all().order_by('-year')

    # Get selected school year or default to newest.
    selected_school_year = request.GET.get('school_year')

    if selected_school_year:
        try:
            school_year = School_year.objects.get(
                SchoolYearID=selected_school_year)
            all_courses = my_courses.filter(school_year=school_year)
        except School_year.DoesNotExist:
            # Fallback to newest.
            school_year = all_school_years.first()
            all_courses = my_courses.filter(
                school_year=school_year) if school_year else Course.objects.none()
    else:
        # Default to newest.
        school_year = all_school_years.first()
        all_courses = my_courses.filter(
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

    # Prepare context. `courses` is the sorted list rather than the queryset,
    # so the template's count costs no second query.
    #
    # The teacher's students, grades and absences used to be built here and
    # were never rendered. They are also not scoped to the selected year,
    # which is why the page shows no total for them: a figure beside a year
    # filter that ignores the year states something untrue.
    context = {
        "courses": sorted_courses,
        'eso_courses': eso_courses,
        'bachillerato_courses': bachillerato_courses,
        'ib_courses': ib_courses,
        # The template renders the three groups in one loop; the labels live
        # here so the empty states can differ per group.
        'course_groups': [
            {'label': 'Eso', 'courses': eso_courses,
             'empty_label': 'No impartes ninguna clase de Eso este año.'},
            {'label': 'Bachillerato', 'courses': bachillerato_courses,
             'empty_label': 'No impartes ninguna clase de Bachillerato este año.'},
            {'label': 'IB', 'courses': ib_courses,
             'empty_label': 'No impartes ninguna clase de IB este año.'},
        ],
        'school_years': all_school_years,
        'selected_school_year': school_year,
    }
    # Render dashboard.
    return render(request, "mainapp/teacher_dashboard.html", context)


@teacher_required
def section_courses(request, section):
    profile = request.user.profile

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
        courses_base_qs = teacher_courses(profile.teacher).filter(
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


@teacher_required
def class_dashboard(request, course_id):
    profile = request.user.profile

    course = get_object_or_404(teacher_courses(profile.teacher), CourseID=course_id)

    # Trimester and subject are the two scope controls; the school year comes
    # from the course itself. See ClassScope for why `?school_year_id=` — which
    # section_courses.html appends — is ignored here.
    scope = resolve_class_scope(
        course,
        trimester_id=request.GET.get('trimester_id'),
        subject_courses_id=request.GET.get('subject_courses_id'),
    )

    if request.method == 'POST':
        form = AusenciaForm(request.POST, scope=scope)

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
            # Carry the scope through the redirect, or the teacher lands back
            # on the default trimester after every save.
            url = reverse('class_dashboard', args=[course.CourseID])
            if scope.query_string:
                url = f'{url}?{scope.query_string}'
            return redirect(url)
        else:
            messages.error(request, 'Errores en el formulario de ausencia.')
    else:
        form = AusenciaForm(scope=scope)

    # A scope change arrives as a boosted GET from the scope bar and only needs
    # the fragment back. Anything else — a first load, a POST that failed
    # validation, JavaScript off — gets the whole page, which is why the scope
    # links stay real <a href>.
    is_hx = request.method == 'GET' and request.headers.get('HX-Request') == 'true'

    context = {
        "course": course,
        "scope": scope,
        "metrics": class_metrics(scope),
        "ausencia_form": form,
        "hx_request": is_hx,
    }
    template = "mainapp/_class_scope.html" if is_hx else "mainapp/class_dashboard.html"
    return render(request, template, context)


@teacher_required
def download_class_list(request, course_id):
    # 1. Get Course object.
    course = get_object_or_404(
        teacher_courses(request.user.profile.teacher), CourseID=course_id)

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

    # The year comes from the course, not from the calendar. Derived from
    # `timezone.now()` this wrote e.g. "2026-2027" for a course held in
    # "2025-2026", and `import_grades` looks years up without creating them —
    # so every row of the template it just handed the teacher failed on
    # re-import with "año escolar no encontrado". `Course.school_year` is the
    # only year this course can have.
    school_year_str = course.school_year.year

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


@teacher_required
def student_dashboard_content(request, student_id):
    # Authentication required.
    profile = request.user.profile

    # Get student.
    student = get_object_or_404(
        teacher_students(profile.teacher), StudentID=student_id)

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

    # Where "back" goes. The legacy page had a <button data-action="back">,
    # which is dead markup on a v2 page — `behaviors.js` is not loaded there —
    # so the breadcrumb links to the class instead. The id arrives from the
    # register's row links (`?course=`), i.e. from the URL, so resolve it
    # against this teacher's own courses rather than trusting it: an
    # unrecognised, non-numeric or unauthorised id simply yields no link.
    return_course = None
    return_course_id = request.GET.get('course')
    if return_course_id:
        try:
            return_course = teacher_courses(profile.teacher).filter(
                CourseID=int(return_course_id)).first()
        except (TypeError, ValueError):
            return_course = None

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
def tutor_dashboard(request):
    """
    Deprecated view. Redirects to student_dashboard which handles
    tutor logic and filtering correctly.
    """
    return redirect('student_dashboard')


@ratelimit(key='user', rate='30/h', block=True)
@role_required('student', 'tutor', 'professor')
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
        # Scoped to this teacher's own students; unlinked teachers get nothing.
        my_students = teacher_students(profile.teacher) if profile.teacher \
            else Students.objects.none()
        if student_id:
            # Professor (specific student).
            student = get_object_or_404(my_students, pk=student_id)
            grades = Grade.objects.filter(student=student)
            filename = f"{student.Name}_notas.csv"
        else:
            # Professor (all their own students).
            grades = Grade.objects.filter(student__in=my_students)
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
    audit(request, 'grades.export', rows=grades.count(), scope=profile.role)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)

    # Write header.
    writer.writerow(
        ['Estudiante', 'Asignatura', 'Trimestre', 'Año Escolar', 'Nota', 'Tipo de Nota', 'Numero tipo de Nota', 'Comentario'])

    # Write rows.
    for grade in grades.select_related(
            'student', 'subject', 'trimester', 'school_year'):
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


@ratelimit(key='user', rate='30/h', block=True)
@teacher_required
def class_grades_download(request, course_id):
    # Download grades for a specific class.
    profile = request.user.profile

    # Get Course.
    course = get_object_or_404(teacher_courses(profile.teacher), CourseID=course_id)

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


@teacher_required
def create_edit_grade(request, grade_id=None, student_id=None):
    """
    Create or edit a grade.
    Pre-selects student and latest school year.
    """
    profile = request.user.profile

    student_instance = None
    grade_instance = None
    initial_data = {}

    # 1. Get latest school year.
    latest_year = School_year.objects.all().order_by('-year').first()

    # Determine edit or create mode.
    my_students = teacher_students(profile.teacher)

    if grade_id:
        # Edit mode.
        grade_instance = get_object_or_404(
            Grade, id=grade_id, student__in=my_students)
        student_instance = grade_instance.student
        initial_data['student'] = student_instance.pk
    elif student_id:
        # Create mode.
        student_instance = get_object_or_404(my_students, pk=student_id)
        initial_data['student'] = student_instance.pk

        # 2. Set default school year.
        if latest_year:
            initial_data['school_year'] = latest_year.pk

    # Handle POST.
    if request.method == "POST":
        form = GradeForm(request.POST, instance=grade_instance)

        if form.is_valid():
            g = form.save(commit=False)
            # The student comes from the URL, never from the request body:
            # `student` is only a hidden input, so a crafted POST could
            # otherwise write a grade onto any student.
            g.student = student_instance
            g.save()
            audit(request, 'grade.save', grade_id=g.pk,
                  student_id=g.student_id, value=g.grade,
                  created=grade_instance is None)

            messages.success(request, "Nota guardada correctamente.")
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

@role_required('professor')
def load_trimesters(request):
    """Return the trimester <option> list for a school year, as markup.

    This answered JSON for a jQuery cascade while `grade_form` was on
    `base.html`. `base_v2` loads htmx and nothing else, so the endpoint now
    returns the fragment htmx swaps straight into `#id_trimester` — same
    route, same name, same role check, one less client-side parser.

    `school_year` is the select's own name (htmx sends an element's value under
    it); `school_year_id` is kept because it is the param every other view in
    this file uses for a year.
    """
    school_year_id = (request.GET.get('school_year')
                      or request.GET.get('school_year_id') or None)

    trimesters = Trimester.objects.filter(
        school_year_id=school_year_id).order_by('Name')

    return render(request, 'mainapp/_trimester_options.html',
                  {'trimesters': trimesters})


@teacher_required
def create_edit_ausencia(request, ausencia_id=None, student_id=None):
    # Create or edit absence.
    profile = request.user.profile

    student_instance = None
    ausencia_instance = None

    # Determine edit or create mode.
    my_students = teacher_students(profile.teacher)

    if ausencia_id:
        # Edit mode.
        ausencia_instance = get_object_or_404(
            Ausencias, id=ausencia_id, student__in=my_students)
        student_instance = ausencia_instance.student
    elif student_id:
        # Create mode.
        student_instance = get_object_or_404(my_students, pk=student_id)

    # Handle POST.
    if request.method == "POST":
        form = AusenciaEditForm(request.POST, instance=ausencia_instance)

        if form.is_valid():
            ausencia = form.save(commit=False)

            # Assign student if new. Indented into the is_valid() branch: at
            # module level these lines ran even on an invalid form, where
            # `ausencia` is unbound, raising UnboundLocalError -> HTTP 500
            # instead of re-rendering the form with its errors.
            if not ausencia_id:
                ausencia.student = student_instance

            ausencia.save()
            audit(request, 'absence.save', absence_id=ausencia.pk,
                  student_id=ausencia.student_id)

            messages.success(request, "Ausencia guardada correctamente.")
            return redirect('student_dashboard_content',
                            student_id=student_instance.pk)
    else:
        # GET request.
        form = AusenciaEditForm(instance=ausencia_instance)

    context = {
        "form": form,
        "is_edit": ausencia_instance is not None,
        "student": student_instance,
    }
    return render(request, "mainapp/ausencia_form.html", context)


@ratelimit(key='user', rate='60/m', block=True)
@teacher_required
def search_students(request):
    profile = request.user.profile

    # Get query and optional course filter.
    query = (request.GET.get('q') or '').strip()
    course_id = request.GET.get('course')

    students_qs = Students.objects.none()

    # Every branch starts from this teacher's own students, never from all.
    my_students = teacher_students(profile.teacher)

    # Filter by course.
    if course_id:
        try:
            course_obj = teacher_courses(profile.teacher).get(
                CourseID=course_id)
            # Filter by Students -> Students_Courses -> Course
            students_qs = my_students.filter(
                students_courses__course_section=course_obj
            ).distinct()
        except Course.DoesNotExist:
            students_qs = Students.objects.none()
    else:
        # No course filter: still scoped to this teacher.
        students_qs = my_students

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
            # Same rule as the register's left rule, so the two lists mark a
            # student identically instead of inventing a second convention.
            'initials': student_initials(s.Name),
        })

    context = {
        'query': query,
        'results': results,
        'course_id': course_id,
    }
    return render(request, 'mainapp/search_results.html', context)


@ratelimit(key='user', rate='10/h', block=True)
@teacher_required
def import_grades(request, course_id=None):
    profile = request.user.profile

    # Get Course.
    course = None
    if course_id:
        course = get_object_or_404(
            teacher_courses(profile.teacher), CourseID=course_id)

    # Rows may only touch students this teacher teaches, and when the import
    # is scoped to a class, only that class. Previously `course` was fetched
    # and then never used, so a class-scoped import could grade anyone.
    importable_students = teacher_students(profile.teacher)
    if course:
        importable_students = importable_students.filter(
            students_courses__course_section=course)

    # Whole-file refusals stay in `messages` — they are one statement about the
    # upload, not a list. Per-row failures go in `result` and are tabulated.
    def page(result=None):
        return render(request, 'mainapp/import_grades.html', {
            'course': course,
            'result': result,
            'max_rows': MAX_IMPORT_ROWS,
            'max_mb': MAX_IMPORT_BYTES // 1024 // 1024,
        })

    result = None

    # Handle POST (CSV upload).
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')

        # Basic validation.
        if not csv_file:
            messages.error(request, 'Por favor selecciona un archivo CSV.')
            return page()

        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'El archivo debe ser un CSV.')
            return page()

        # Django's upload settings do not cap file size: DATA_UPLOAD_MAX_
        # MEMORY_SIZE excludes files and FILE_UPLOAD_MAX_MEMORY_SIZE is only a
        # spool threshold. Without this a 2 GB upload is accepted and read
        # into memory whole.
        if csv_file.size > MAX_IMPORT_BYTES:
            messages.error(
                request,
                f'El archivo supera el límite de '
                f'{MAX_IMPORT_BYTES // 1024 // 1024} MB.')
            return page()

        # Counters.
        created_count = 0
        updated_count = 0
        error_count = 0
        errors = []

        # Errors used to be pushed into `messages` one banner per row, capped
        # at ten, which is the page's actual job rendered as a wall of text.
        # They are structured instead and rendered as a table, so the row
        # number is a column rather than a prefix. The limit is a display
        # limit only — `error_count` still counts every failed row.
        ERROR_DISPLAY_LIMIT = 50

        def add_error(message, row_num=None):
            """Record one row failure. Messages must never echo a student
            name: they are shown in the browser and a roster is PII."""
            errors.append({'row': row_num, 'message': message})

        try:
            # Streamed, not read() into memory. iterdecode keeps peak memory
            # proportional to one row rather than to the whole file.
            reader = csv.DictReader(codecs.iterdecode(csv_file, 'utf-8'))

            # Iterate rows.
            for row_num, row in enumerate(reader, start=2):
                if row_num - 1 > MAX_IMPORT_ROWS:
                    # Not row-scoped: it is a statement about the file, so it
                    # carries no row number.
                    add_error(
                        f'Se ha alcanzado el límite de {MAX_IMPORT_ROWS} filas; '
                        f'el resto del archivo se ha ignorado.')
                    error_count += 1
                    break
                try:
                    # `or` binds tighter than the method call, so
                    # `a or b.strip()` only ever strips the fallback. Wrap the
                    # whole expression instead.
                    student_name = _cell(row, 'Nombre_Estudiante', 'student_name')
                    subject_name = _cell(row, 'Asignatura', 'subject_name')
                    trimester_name = _cell(row, 'Trimestre', 'trimester_name')
                    school_year_str = _cell(row, 'Año_Escolar', 'school_year')
                    grade_raw = _cell(row, 'Nota', 'grade')
                    grade_type = _cell(row, 'Tipo_Nota', 'grade_type') or 'examen'
                    number_raw = _cell(row, 'Numero_Tipo_Nota', 'grade_type_number')
                    comments = _cell(row, 'Comentarios', 'comments')

                    # A blank grade used to import as 0.0, silently recording a
                    # zero for every row the teacher left empty in the template.
                    if not grade_raw:
                        add_error('Falta la nota.', row_num)
                        error_count += 1
                        continue
                    grade_value = float(grade_raw.replace(',', '.'))
                    grade_type_number = int(number_raw or 0)

                    # Scoped: a professor may only write grades for students
                    # they actually teach, whatever the uploaded file names.
                    student = importable_students.get(Name=student_name)
                    subject = Subjects.objects.get(Name=subject_name)
                    # Looked up, never created: an upload must not be able to
                    # invent school years or trimesters.
                    school_year = School_year.objects.get(year=school_year_str)
                    trimester = Trimester.objects.get(
                        Name=int(trimester_name), school_year=school_year)

                    with transaction.atomic():
                        key = dict(
                            student=student, subject=subject,
                            trimester=trimester, school_year=school_year,
                            grade_type=grade_type,
                            grade_type_number=grade_type_number)
                        grade = Grade.objects.filter(**key).first()
                        created = grade is None
                        if created:
                            grade = Grade(**key)
                        grade.grade = grade_value
                        grade.comments = comments
                        # update_or_create skips validators entirely, which is
                        # how out-of-range grades and invalid grade types got in.
                        grade.full_clean()
                        grade.save()

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                except Students.DoesNotExist:
                    add_error(
                        'Alumno no encontrado o fuera de tu clase.', row_num)
                    error_count += 1
                except Subjects.DoesNotExist:
                    add_error('Asignatura no encontrada.', row_num)
                    error_count += 1
                except School_year.DoesNotExist:
                    # Name the value. Years and trimesters are not PII, and a
                    # bare "no encontrado" gave the teacher nothing to act on —
                    # this is the error the broken template used to raise on
                    # every single row.
                    # The lowercase "año escolar" is load-bearing: the
                    # round-trip test asserts a *successful* import never says
                    # it, and capitalising here would make that test vacuous.
                    add_error(
                        f'El año escolar «{school_year_str}» no existe.',
                        row_num)
                    error_count += 1
                except Trimester.DoesNotExist:
                    add_error(
                        f'El trimestre «{trimester_name}» no existe en el '
                        f'año escolar «{school_year_str}».', row_num)
                    error_count += 1
                except ValidationError:
                    add_error(
                        'Valores no válidos (nota fuera de 0-10, o tipo de '
                        'nota incorrecto).', row_num)
                    error_count += 1
                except (ValueError, TypeError):
                    add_error('Formato numérico no válido.', row_num)
                    error_count += 1
                except Exception:
                    add_error('Error al procesar la fila.', row_num)
                    error_count += 1

            audit(request, 'grades.import', created=created_count,
                  updated=updated_count, errors=error_count,
                  course_id=course.pk if course else None)

            # The summary is rendered on the page rather than pushed through
            # `messages`: three counters and a table read at a glance, where
            # thirteen stacked banners did not.
            result = {
                'created': created_count,
                'updated': updated_count,
                'error_count': error_count,
                'rows': created_count + updated_count + error_count,
                'errors': errors[:ERROR_DISPLAY_LIMIT],
                'hidden_errors': max(len(errors) - ERROR_DISPLAY_LIMIT, 0),
            }

        except Exception:
            messages.error(
                request,
                'No se ha podido leer el archivo. Comprueba que es un CSV '
                'válido codificado en UTF-8.')

    return page(result)


@role_required('administrator')
def adminage_dashboard_view(request):
    profile = request.user.profile

    context = {
        'title': 'School Admin Dashboard',
        'school_years': School_year.objects.all().order_by('-year')
    }
    return render(request, "adminage/adminage_dashboard.html", context)


# =======================================================
# --- VIEW 1: CREATE SCHOOL YEAR ---
# =======================================================
@role_required('administrator')
def create_school_year_view(request):
    profile = request.user.profile

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
@role_required('administrator')
def create_courses_sections_view(request):
    profile = request.user.profile

    # Get School Year ID.
    school_year_id = request.GET.get('school_year_id')

    # Validate flow.
    if not school_year_id:
        messages.error(request, "Empieza por dar de alta un año escolar.")
        return redirect('adminage_dashboard')

    # Get School Year object.
    try:
        school_year = School_year.objects.get(pk=school_year_id)
    except (School_year.DoesNotExist, ValueError):
        # `ValueError` is not redundant: an id that is not a number at all —
        # `?school_year_id=abc`, from a hand-edited URL or a stale bookmark —
        # never reaches `DoesNotExist`, because the ORM raises while adapting
        # the value to the primary key's type. Catching only `DoesNotExist`
        # turned that into a 500. Same shape as `reassign_students`.
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

        # The type the formset validates its hidden level numbers against is
        # the *validated* one, not `request.POST.get('course_tipo')` — see
        # CourseSectionForm.clean_main_course_name.
        course_tipo = form_main.cleaned_data.get('course_tipo') or course_tipo

        CourseFormSet = formset_factory(CourseSectionForm, extra=0)
        formset = CourseFormSet(
            request.POST, form_kwargs={'course_tipo': course_tipo})

        if formset.is_valid():
            # Which sections this year already holds for this type. Running the
            # flow a second time for the same type and level is an ordinary
            # thing to do — an administrator adding a section C to a level that
            # already has A and B has no other route — and it used to produce a
            # duplicate row rather than either skipping or refusing. Now that
            # (Tipo, Section, school_year) is unique, an unfiltered bulk_create
            # would raise instead, which is not an improvement.
            existing = set(Course.objects.filter(
                school_year=school_year, Tipo=course_tipo
            ).values_list('Section', flat=True))

            num_created = 0
            skipped = []

            for form_section in formset:
                if form_section.cleaned_data:
                    main_course_name = form_section.cleaned_data['main_course_name']
                    num_subsections = form_section.cleaned_data['num_subsections']
                    # Generate letters (A=65, B=66...).
                    subsection_letters = [chr(65 + i)
                                          for i in range(num_subsections)]

                    new_courses = []
                    for letter in subsection_letters:
                        section = f"{main_course_name}{letter}"
                        if section in existing:
                            skipped.append(section)
                            continue
                        existing.add(section)
                        new_courses.append(
                            Course(
                                Tipo=course_tipo,
                                Section=section,
                                school_year=school_year
                            )
                        )
                    Course.objects.bulk_create(new_courses)
                    num_created += len(new_courses)

            messages.success(
                request,
                f"{num_created} sección(es) creada(s) para {course_tipo} ({school_year}).")

            if skipped:
                # Named rather than counted: "3 ya existían" leaves the
                # administrator guessing which three, and the answer decides
                # whether anything is wrong.
                messages.info(
                    request,
                    "Ya existían y no se han duplicado: "
                    + ", ".join(f"{course_tipo} {name}"
                                for name in sorted(skipped)) + ".")

            return redirect('adminage_dashboard')

        else:
            messages.error(request, "Corrige los errores del formulario.")
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
    # No gate: only called from create_courses_sections_view, already gated.
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


@role_required('administrator')
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

        except (School_year.DoesNotExist, ValueError):
            # See create_courses_sections_view: a non-numeric id raises
            # ValueError out of the ORM, not DoesNotExist, so the Spanish
            # branch below was unreachable for the commonest bad input.
            messages.error(request, "El año escolar no es válido.")
            return redirect('assign_subjects')

    # Get student links if course selected.
    if selected_course_id:
        try:
            target_course = Course.objects.get(pk=selected_course_id)
            course_students_links = Students_Courses.objects.filter(
                course_section=target_course
            ).select_related('student').order_by('student__Name')
        except (Course.DoesNotExist, ValueError):
            messages.warning(
                request, "El identificador de curso no es válido.")
            selected_course_id = None
            target_course = None

    # 2. HANDLE POST (Create/Update Assignment)
    if request.method == 'POST':

        selected_course_id = request.POST.get('course_id')
        school_year_id_post = request.POST.get('school_year_id')
        final_school_year_id = school_year_id_post or school_year_id

        if not selected_course_id or not final_school_year_id:
            messages.error(
                request, "Selecciona un curso y un año escolar.")
            return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}')

        try:
            target_course = Course.objects.get(pk=selected_course_id)
        except (Course.DoesNotExist, ValueError):
            messages.error(request, "El curso no es válido.")
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
                    request, "Selecciona al menos un trimestre.")
                current_form = form
                return redirect(reverse('assign_subjects') + f'?school_year_id={final_school_year_id}&course_id={selected_course_id}')

            try:
                selected_trimesters = Trimester.objects.filter(
                    pk__in=trimester_ids_selected,
                    school_year__pk=final_school_year_id
                )
            except ValueError:
                messages.error(
                    request, "Los trimestres seleccionados no son válidos.")
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
                    request, "Los alumn@s seleccionad@s no son válid@s.")
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
                    request, f"Asignatura {subject.Name} asignada en {len(newly_created_objects)} trimestre(s) para {student_count} alumn@(s).")

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

    # The cascade is rendered server-side on every paint, so the page is
    # usable before any JavaScript runs — the same fix GradeForm.__init__
    # needed when it was ignoring `initial` and rendering an empty trimester
    # select. htmx only saves the round trip between the three selects.
    course_type = request.GET.get('course_type') or ''
    level = request.GET.get('level') or ''

    # Arriving with only ?course_id= — from a bookmark, or from the redirect
    # this view does after a successful POST — is the normal case, and the
    # type and level are recoverable from the course itself. This is what the
    # old template's LEVEL_LOOKUP round trip was meant to do and never did.
    if target_course:
        course_type = course_type or target_course.Tipo
        if not level and target_course.Section[:1].isdigit():
            level = target_course.Section[:1]

    context = {
        'title': 'Asignar Asignaturas a Clases',
        'form': current_form,
        'course_types': course_types,
        'school_year_id': school_year_id,
        'selected_course_id': selected_course_id,
        'selected_course_type': course_type,
        'current_school_year': current_school_year,
        'trimesters': trimesters,
        # The course the page is actually scoped to. It was computed here and
        # used through the whole view but never handed to the template, so both
        # `{% if target_course %}` branches in assign_subjects.html were dead:
        # the badge read "Sección seleccionada · Ninguna" over that section's
        # own roster, and "Esta sección no tiene alumn@s matriculad@s" could
        # never win over "Elija una sección arriba". `reassign_students` passes
        # `origin_course` for exactly this and is pinned by
        # test_the_two_empty_states_do_not_read_alike; this page had no
        # equivalent, which is why a green suite never noticed.
        'target_course': target_course,
        # Lista de registros de estudiante-curso
        'course_students_links': course_students_links,
    }
    context.update(course_cascade_context(
        school_year_id, course_type, level, selected_course_id))
    return render(request, "adminage/assign_subjects.html", context)


# =======================================================
# B. Endpoint htmx para la cascada Tipo -> Nivel -> Sección
# =======================================================
def _course_levels(school_year_id, course_type):
    """The levels of a course type that actually have a section created.

    `MAIN_COURSES` fixes which levels a type may have (Eso 1-4, Bachillerato
    and IB 1-2); this narrows that to the ones a `Course` row exists for, so
    the select never offers a level with nothing behind it.
    """
    if not school_year_id or not course_type:
        return []

    try:
        names = set(Course.objects.filter(
            school_year__pk=school_year_id, Tipo=course_type
        ).values_list('Section', flat=True))
    except (ValueError, TypeError):
        # A non-numeric id in the query string is a bad request, not a 500.
        return []

    return [{'value': str(lvl), 'text': f'{lvl}º {course_type}'}
            for lvl in MAIN_COURSES.get(course_type, [])
            if any(name.startswith(str(lvl)) for name in names)]


def _course_sections(school_year_id, course_type, level):
    """The final sections under one level, e.g. 1A and 1B under Eso 1."""
    if not school_year_id or not course_type or not level:
        return []

    try:
        rows = Course.objects.filter(
            school_year__pk=school_year_id, Tipo=course_type,
            Section__startswith=level,
        ).order_by('Section').values('CourseID', 'Section')
        return [{'value': str(row['CourseID']), 'text': row['Section']}
                for row in rows]
    except (ValueError, TypeError):
        return []


def course_cascade_context(school_year_id, course_type, level, course_id):
    """Everything `adminage/_course_dependents.html` needs, for either caller.

    The partial is rendered two ways — inlined by assign_subjects.html on the
    first paint, and returned by `load_course_sections` for the htmx swap — and
    both go through this one function. That is what stops the two renderings
    from drifting; `mainapp/_trimester_options.html` states the same rule for
    the trimester cascade, where a mismatch would silently rename every option.
    """
    return {
        'school_year_id': school_year_id,
        'level_options': _course_levels(school_year_id, course_type),
        'section_options': _course_sections(
            school_year_id, course_type, level),
        'selected_level': level or '',
        'selected_course_id': str(course_id or ''),
    }


@role_required('administrator')
def load_course_sections(request):
    """The course cascade, as markup rather than JSON.

    Same route and same `@role_required('administrator')` as before; only the
    response shape changed. It now renders the two dependent selects and htmx
    swaps them in, which is what `ajax_load_trimesters` does for the trimester
    cascade — `base_v2` ships htmx and nothing else, so the jQuery that
    consumed the old JSON is gone with `base.html`.

    It returns the whole dependent block rather than a bare list of <option>s,
    and that is deliberate. Changing the course type has to repopulate the
    level select *and* clear the section select, which is two targets; htmx
    allows one `hx-target` per element, and the out-of-band alternative cannot
    be mixed with bare <option>s in one response, because htmx wraps a
    fragment that starts with <option> in a <select> to make the browser parse
    it at all. One block, one target, one response shape.

    The `LEVEL_LOOKUP` mode the old template asked for is not reimplemented:
    this view never had it. It was added template-side only in 56978a8, no
    branch has ever read `course_id_lookup`, so the "restore the dropdowns from
    a preselected ?course_id=" path has never once run. The rebuilt page does
    that server-side from the query string instead, which is both simpler and
    the only version that works before any JavaScript does.
    """
    context = course_cascade_context(
        request.GET.get('school_year_id'),
        request.GET.get('course_type'),
        request.GET.get('level'),
        request.GET.get('course_id'),
    )
    return render(request, 'adminage/_course_dependents.html', context)


# =======================================================
# --- SINGLE VIEW: CREATE STUDENT AND ASSIGN CLASS ---
# =======================================================
@role_required('administrator')
def create_and_assign_student_view(request):
    """
    Creates a new student and assigns them to a course section.
    """

    course_types = Course.COURSE_TYPE_CHOICES

    # Not `.only('pk')` any more: the rebuilt page states which year it is
    # filing the student under, and a deferred field would fetch the row a
    # second time to say so.
    latest_school_year = School_year.objects.order_by('-year').first()
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
                    request, "Selecciona una sección a la que asignar.")
            else:
                try:
                    # Get selected Course.
                    target_course = Course.objects.get(pk=course_id)
                except (Course.DoesNotExist, ValueError):
                    # A crafted or stale `course_id` that is not a number
                    # raises ValueError, not DoesNotExist. Nothing is written
                    # before this point — the student row is created inside the
                    # `else` below — so this is availability, not integrity.
                    messages.error(
                        request, "La sección seleccionada no es válida.")
                else:
                    # 1. Create Student.
                    new_student = current_form.save()
                    audit(request, 'student.create',
                          student_id=new_student.pk)

                    # 2. Create Relation (Students_Courses).
                    Students_Courses.objects.create(
                        student=new_student,
                        course_section=target_course
                    )

                    messages.success(
                        request, f"Alumn@ «{new_student.Name}» dad@ de alta y asignad@ a {target_course.Section}.")

                    # Redirect to clear form.
                    return redirect('create_and_assign_student')

        else:
            messages.error(
                request, "Revisa los datos del alumn@ (nombre y correo).")

    # Every section of the current year, grouped by course type for <optgroup>.
    #
    # This replaces the three-step Tipo -> Nivel -> Sección cascade the page
    # used to run through `load_course_sections`. The cascade cannot be made
    # to work before JavaScript does *here*, unlike on assign_subjects: that
    # page can fall back to submitting its filter bar as a GET, whereas this
    # one is a single POST form and any GET round trip would either discard
    # the name and e-mail already typed or put them in the query string. One
    # grouped select needs no round trip at all, so there is nothing left to
    # degrade. The counts below are `len()` of rows already fetched.
    section_groups = []
    for key, label in course_types:
        courses = list(Course.objects.filter(
            school_year__pk=latest_school_year_id, Tipo=key
        ).order_by('Section')) if latest_school_year_id else []
        if courses:
            section_groups.append({'label': label, 'courses': courses})

    context = {
        'title': 'Create Student and Assign Class',
        'form': current_form,
        'course_types': course_types,
        'current_school_year': latest_school_year,
        'current_school_year_id': latest_school_year_id,
        'section_groups': section_groups,
        # Pass selected course ID if POST failed.
        'selected_course_id': request.POST.get('course_id', ''),
    }
    return render(request, "adminage/create_and_assign_student.html", context)


def _destination_groups():
    """Every course in the app, grouped by year and type, for the row selects.

    All years, not just the current one. Promoting a student into next year's
    class is the case that made the year bug matter — see the POST branch — and
    restricting the destinations to one year would remove the only reason this
    view has to care which enrolment it writes to.

    Ordered in the database rather than through `sort_key_section`, which
    raises on any `Section` not shaped <digit><letter>. A page that lists every
    course in the installation is the wrong place to be strict about that.
    """
    groups = []
    current = None
    for course in Course.objects.select_related('school_year').order_by(
            '-school_year__year', 'Tipo', 'Section'):
        label = f'{course.school_year.year} · {course.Tipo}'
        if current is None or current['label'] != label:
            current = {'label': label, 'courses': []}
            groups.append(current)
        current['courses'].append(course)
    return groups


def _reassign_url(school_year_id, course_id):
    """The reassign page, back in the scope it was posted from.

    The redirect used to drop the scope entirely, which put the administrator
    back at an empty picker after every save. Landing on the origin class again
    is also the only way to see that the move happened: the students that moved
    are gone from the roster.
    """
    params = {key: value for key, value in (
        ('school_year_id', school_year_id), ('course_id', course_id)) if value}
    url = reverse('reassign_students')
    return f'{url}?{urlencode(params)}' if params else url


@role_required('administrator')
def reassign_students(request):
    """
    Main view to reassign students from one class to another.
    """
    if request.method == 'POST':
        # Process reassignment.
        # List of "student_id:course_id"
        assignments = request.POST.getlist('assignments')
        audit(request, 'roster.reassign', count=len(assignments))

        success_count = 0
        error_count = 0

        for assignment in assignments:
            if not assignment or assignment == ':':
                continue

            try:
                student_id, new_course_id = assignment.split(':')
                student = Students.objects.get(StudentID=student_id)
                new_course = Course.objects.get(CourseID=new_course_id)

                # The enrolment being moved is the one for the destination's
                # own school year, not "whichever row comes back first".
                #
                # Students_Courses is unique on (student, course_section), so a
                # student holds one row per course and therefore one per year
                # they have been enrolled. The previous lookup was
                # `.filter(student=student).first()` with no ordering: it
                # repointed an arbitrary enrolment, which for anyone who has
                # progressed a year could be a previous year's class. It also
                # went unnoticed because a student who has only ever been in
                # one course has exactly one row, which is every student in a
                # freshly seeded database.
                same_year = Students_Courses.objects.filter(
                    student=student,
                    course_section__school_year=new_course.school_year,
                )

                if same_year.filter(course_section=new_course).exists():
                    # Already in the destination. Asking "is any row already
                    # the destination?" rather than "is the *first* row the
                    # destination?" is the whole fix: with two enrolments in
                    # that year, `.first()` returned the other one, repointing
                    # it tripped unique_together, and a request whose state was
                    # already correct was reported as "no se pudieron aplicar".
                    pass
                else:
                    existing_assignment = same_year.order_by('pk').first()

                    if existing_assignment is None:
                        # No enrolment for that year yet: this is an addition,
                        # not a move.
                        Students_Courses.objects.create(
                            student=student,
                            course_section=new_course
                        )
                    else:
                        existing_assignment.course_section = new_course
                        existing_assignment.save()

                success_count += 1

            except (Students.DoesNotExist, Course.DoesNotExist, ValueError):
                # A malformed "sid:cid" pair, or an id naming nothing. The
                # payload is built client-side, so this is a bad request
                # rather than a server fault.
                error_count += 1
            except IntegrityError:
                # unique_together on (student, course_section). Reachable if
                # the student already holds a row for the destination course
                # under a *different* year's enrolment being moved onto it.
                error_count += 1

        if success_count > 0:
            messages.success(
                request,
                f"{success_count} alumn@(s) reasignad@(s) correctamente.")
        if error_count > 0:
            messages.warning(
                request,
                f"{error_count} reasignación(es) no se pudieron aplicar.")
        if success_count == 0 and error_count == 0:
            # Every row left on «Sin cambios» submits an empty value, which the
            # loop above skips, so neither message fired and the redirect was
            # indistinguishable from a save that worked. Saying nothing after a
            # submit is the one outcome a form must never have.
            messages.info(
                request,
                "No se seleccionó ningún cambio: no se ha reasignado a nadie.")

        return redirect(_reassign_url(request.POST.get('school_year_id'),
                                      request.POST.get('course_id')))

    # GET: pick an origin class, then a destination for each of its students.
    #
    # The origin cascade is the shared one — `load_course_sections` feeding
    # `adminage/_course_dependents.html` — rather than the four bespoke
    # `ajax_get_*` endpoints this page used to own. Those spoke a different
    # dialect (a course as type + number + letter, reassembled into a Section
    # string) and had no other consumer, so they are gone with the template
    # that called them.
    school_years = School_year.objects.all().order_by('-year')
    course_types = Course.COURSE_TYPE_CHOICES

    school_year_id = request.GET.get('school_year_id') or ''
    course_type = request.GET.get('course_type') or ''
    level = request.GET.get('level') or ''
    selected_course_id = request.GET.get('course_id') or ''

    origin_course = None
    if selected_course_id:
        try:
            origin_course = Course.objects.select_related('school_year').get(
                pk=selected_course_id)
        except (Course.DoesNotExist, ValueError):
            # A hand-edited URL or a stale bookmark, not a server fault.
            messages.warning(
                request, "El identificador de curso no es válido.")
            selected_course_id = ''

    if origin_course is not None and school_year_id and (
            school_year_id != str(origin_course.school_year_id)):
        # The year and the course disagree, and the year wins.
        #
        # With JavaScript off, the scope bar is an ordinary GET form: choosing
        # a different year and pressing «Cargar alumnado» resubmits the section
        # select untouched, so a `course_id` from the *old* year travels with
        # the new year. Letting the course override then silently reverted the
        # only thing the administrator had just changed — and
        # `_course_dependents.html` says in as many words that the no-JS path
        # works. The year is the deliberate choice of the two, so the stale
        # course is what gets dropped; type and level survive, because they are
        # not year-specific and the section select repopulates under them.
        messages.info(
            request,
            "La clase elegida pertenece a otro año escolar. "
            "Seleccione una sección del año escolar actual.")
        origin_course = None
        selected_course_id = ''

    if origin_course is not None:
        # Arriving with only ?course_id= is the normal case — a bookmark, or
        # this view's own post-POST redirect — and every select above it is
        # recoverable from the row, so all four come back filled before htmx
        # runs once. The row *overrides* the query string rather than filling
        # gaps in it: a `?level=` disagreeing with the course would describe a
        # class that does not exist, which is the same rule
        # `resolve_class_scope` applies on the teacher's side.
        #
        # `level` used only to be filled when absent, which left
        # `?course_id=<Bachillerato 1A>&level=4` rendering the heading and the
        # hidden course_id as Bachillerato 1A while Nivel showed nothing
        # selected and Sección said «Este nivel no tiene secciones» — the scope
        # bar contradicting the page under it.
        school_year_id = str(origin_course.school_year_id)
        course_type = origin_course.Tipo
        level = (origin_course.Section[:1]
                 if origin_course.Section[:1].isdigit() else '')

    roster = []
    if origin_course is not None:
        roster = [
            {'student': link.student,
             'initials': student_initials(link.student.Name)}
            for link in Students_Courses.objects.filter(
                course_section=origin_course
            ).select_related('student').order_by('student__Name')
        ]

    # Only paid for when there is a roster to attach them to.
    destination_groups = _destination_groups() if roster else []
    destinations = sum(len(group['courses']) for group in destination_groups)

    context = {
        'school_years': school_years,
        'course_types': course_types,
        'selected_course_type': course_type,
        'origin_course': origin_course,
        'roster': roster,
        'destination_groups': destination_groups,
        # The only course in the installation is the one they are already in,
        # so every row's select offers exactly one option and it is a no-op.
        # Found by rendering the page against the live database, where that is
        # the actual state: a page that offers a control which cannot do
        # anything should say which of the two it is.
        'no_other_destination': bool(roster) and destinations <= 1,
    }
    context.update(course_cascade_context(
        school_year_id, course_type, level, selected_course_id))

    return render(request, 'adminage/reassign_students.html', context)
