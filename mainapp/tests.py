"""Access-control tests.

Covers the two failure modes that shipped: endpoints with no authorization at
all, and denials returned as HTTP 200 (indistinguishable from success to any
test or monitor).

Note: these use `force_login`, not `client.login()`. django-axes' backend
requires a `request` argument that `client.login()` does not supply.
"""

import csv
import io
import pathlib
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .views import MAX_IMPORT_BYTES, class_metrics, resolve_class_scope
from .models import (
    Ausencias, Course, Grade, Profile, School_year, Students,
    Students_Courses, Subjects, Subjects_Courses, Teachers, Trimester,
)

PW = 'test-password'

ADMIN_ONLY = [
    '/reassign-students/',
    '/adminage/',
    '/adminage/assign-subjects/',
    '/adminage/create-student-class/',
    '/adminage/create-school-year/',
    '/ajax/load-sections/?school_year_id=1&course_type=Eso',
]

PROFESSOR_ONLY = [
    '/teacher/',
    '/section/eso/courses/',
    '/search/?q=a',
    '/import/grades/',
    '/ajax/load-trimesters/?school_year_id=1',
]


class AccessControlTestCase(TestCase):
    """Shared fixtures: one user per role, plus a user with no Profile."""

    @classmethod
    def setUpTestData(cls):
        cls.year = School_year.objects.create(year='2025-2026')
        cls.trimester = Trimester.objects.create(Name=1, school_year=cls.year)
        cls.course = Course.objects.create(
            Tipo='Eso', Section='1A', school_year=cls.year)
        cls.other_course = Course.objects.create(
            Tipo='Eso', Section='2B', school_year=cls.year)

        cls.student = Students.objects.create(
            Name='Ana Lopez', Email='ana@example.com')
        cls.other_student = Students.objects.create(
            Name='Beto Ruiz', Email='beto@example.com')
        cls.enrolment = Students_Courses.objects.create(
            student=cls.student, course_section=cls.course)
        Students_Courses.objects.create(
            student=cls.other_student, course_section=cls.other_course)

        cls.subject = Subjects.objects.create(Name='Matematicas')

        # Two teachers, each assigned to a different course section. This is
        # what makes wrong-user (as opposed to wrong-role) tests expressible.
        cls.teacher_a = Teachers.objects.create(Name='Profesora A')
        cls.teacher_b = Teachers.objects.create(Name='Profesor B')
        for teacher, course in ((cls.teacher_a, cls.course),
                                (cls.teacher_b, cls.other_course)):
            Subjects_Courses.objects.create(
                subject=cls.subject, teacher=teacher, course=course,
                trimester=cls.trimester)

        cls.admin = cls._user('admin1', 'administrator')
        cls.professor = cls._user('prof1', 'professor', teacher=cls.teacher_a)
        cls.other_professor = cls._user(
            'prof2', 'professor', teacher=cls.teacher_b)
        # A professor with no Teachers link: must see nothing, not everything.
        cls.unlinked_professor = cls._user('prof0', 'professor')
        cls.pupil = cls._user('alum1', 'student', student=cls.student)
        cls.tutor = cls._user('tut1', 'tutor', children=[cls.student])
        # Deliberately has no Profile row: Profiles are created by hand in
        # Django admin, so this is a reachable state, not a hypothetical.
        cls.profileless = User.objects.create_user('sinperfil', password=PW)

    @classmethod
    def _user(cls, username, role, student=None, children=(), teacher=None):
        user = User.objects.create_user(username, password=PW)
        profile = Profile.objects.create(
            user=user, role=role, student=student, teacher=teacher)
        if children:
            profile.children.set(children)
        return user

    def as_(self, user):
        self.client.force_login(user)
        return self.client


class AnonymousAccessTests(AccessControlTestCase):
    """No endpoint may serve content to an unauthenticated caller."""

    def test_all_protected_urls_redirect_anonymous(self):
        for url in ADMIN_ONLY + PROFESSOR_ONLY:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)

    def test_anonymous_post_cannot_reassign_students(self):
        """The regression that mattered most: an unauthenticated roster write."""
        before = self.enrolment.course_section_id
        response = self.client.post(
            '/reassign-students/',
            {'assignments': [f'{self.student.pk}:{self.other_course.pk}']})

        self.assertEqual(response.status_code, 302)
        self.enrolment.refresh_from_db()
        self.assertEqual(self.enrolment.course_section_id, before)

    def test_anonymous_cannot_read_student_pii(self):
        """`/ajax/get-students/` answered a roster as JSON and is deleted with
        the template that was its only caller. The claim is unchanged; it now
        points at the surface that actually carries the names and e-mail
        addresses, which is the reassign page itself."""
        response = self.client.get(
            f'/reassign-students/?course_id={self.course.pk}')

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b'ana@example.com', response.content)


class RoleEnforcementTests(AccessControlTestCase):
    """Wrong role must be refused, and refused with a 4xx status."""

    def test_admin_urls_reject_other_roles(self):
        for url in ADMIN_ONLY:
            for user in (self.professor, self.pupil, self.tutor):
                with self.subTest(url=url, user=user.username):
                    self.assertEqual(self.as_(user).get(url).status_code, 403)

    def test_professor_urls_reject_other_roles(self):
        for url in PROFESSOR_ONLY:
            for user in (self.admin, self.pupil, self.tutor):
                with self.subTest(url=url, user=user.username):
                    self.assertEqual(self.as_(user).get(url).status_code, 403)

    def test_owning_role_still_reaches_its_own_urls(self):
        """Guards against over-gating: the fix must not break legitimate use."""
        for url in ADMIN_ONLY:
            with self.subTest(url=url):
                self.assertEqual(self.as_(self.admin).get(url).status_code, 200)
        for url in PROFESSOR_ONLY:
            with self.subTest(url=url):
                self.assertEqual(
                    self.as_(self.professor).get(url).status_code, 200)

    def test_student_role_cannot_create_students(self):
        before = Students.objects.count()
        response = self.as_(self.pupil).post(
            '/adminage/create-student-class/',
            {'Name': 'Injected', 'Email': 'x@example.com',
             'course_id': self.course.pk})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Students.objects.count(), before)

    def test_denial_is_not_reported_as_success(self):
        """A denial returning 200 makes every access-control test vacuous."""
        self.assertNotEqual(
            self.as_(self.pupil).get('/teacher/').status_code, 200)


class MissingProfileTests(AccessControlTestCase):
    """A User without a Profile used to raise, producing a 500."""

    def test_profileless_user_is_logged_out_not_crashed(self):
        for url in ['/teacher/', '/adminage/', '/student/1/grade/new/']:
            with self.subTest(url=url):
                self.assertEqual(
                    self.as_(self.profileless).get(url).status_code, 302)


class DataScopingTests(AccessControlTestCase):
    """Scoping that already worked. These exist so it keeps working."""

    def test_tutor_child_selector_is_clamped(self):
        """?child= indexes the tutor's own children; it is not a student id."""
        for value in ('999', '-1', 'not-a-number'):
            with self.subTest(child=value):
                response = self.as_(self.tutor).get(f'/student/?child={value}')
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, self.other_student.Name)

    def test_student_csv_ignores_url_student_id(self):
        """A student hitting another student's CSV url gets only their own."""
        response = self.as_(self.pupil).get(
            f'/grades/csv/{self.other_student.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.other_student.Name.encode(), response.content)

    def test_csv_export_reachable_by_every_entitled_role(self):
        """Over-gating check: student and tutor own this button too."""
        for user in (self.pupil, self.tutor, self.professor):
            with self.subTest(user=user.username):
                response = self.as_(user).get('/grades/csv/')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'text/csv')


class ObjectLevelScopingTests(AccessControlTestCase):
    """Wrong-user, same-role. A professor may only reach their own students.

    These could not be written before Profile.teacher existed: there was no
    way to express "professor A" as distinct from "professor B".
    """

    def test_professor_cannot_open_another_teachers_student(self):
        response = self.as_(self.professor).get(
            f'/students/{self.other_student.pk}/dashboard/')
        self.assertEqual(response.status_code, 404)

    def test_professor_cannot_open_another_teachers_course(self):
        for url in [f'/class/{self.other_course.pk}/dashboard/',
                    f'/download/class-list/{self.other_course.pk}/',
                    f'/class/{self.other_course.pk}/grades/download/']:
            with self.subTest(url=url):
                self.assertEqual(
                    self.as_(self.professor).get(url).status_code, 404)

    def test_professor_can_open_their_own_course(self):
        """The same URLs must still work for the teacher who owns them."""
        for url in [f'/class/{self.course.pk}/dashboard/',
                    f'/download/class-list/{self.course.pk}/',
                    f'/class/{self.course.pk}/grades/download/',
                    f'/students/{self.student.pk}/dashboard/']:
            with self.subTest(url=url):
                self.assertEqual(
                    self.as_(self.professor).get(url).status_code, 200)

    def test_professor_cannot_grade_another_teachers_student(self):
        response = self.as_(self.professor).get(
            f'/student/{self.other_student.pk}/grade/new/')
        self.assertEqual(response.status_code, 404)

    def test_professor_cannot_edit_another_teachers_grade(self):
        grade = Grade.objects.create(
            student=self.other_student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            grade=5, grade_type='examen')
        response = self.as_(self.professor).get(
            f'/student/edit/grade/{grade.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_professor_cannot_edit_another_teachers_absence(self):
        absence = Ausencias.objects.create(
            student=self.other_student, subject=self.subject,
            trimester=self.trimester, school_year=self.year, Tipo='Ausencia')
        response = self.as_(self.professor).get(
            f'/student/edit/ausencia/{absence.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_bulk_csv_export_contains_only_own_students(self):
        """The sharpest case: /grades/csv/ used to return Grade.objects.all()."""
        Grade.objects.create(
            student=self.other_student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            grade=9, grade_type='examen')
        body = self.as_(self.professor).get('/grades/csv/').content

        self.assertNotIn(self.other_student.Name.encode(), body)

    def test_search_returns_only_own_students(self):
        response = self.as_(self.professor).get('/search/?q=e')

        self.assertContains(response, self.student.Name)
        self.assertNotContains(response, self.other_student.Name)

    def test_dashboard_lists_only_own_courses(self):
        """The dashboard renders course sections, so assert on those."""
        response = self.as_(self.professor).get('/teacher/')

        self.assertContains(response, f'/class/{self.course.pk}/dashboard/')
        self.assertNotContains(
            response, f'/class/{self.other_course.pk}/dashboard/')

    def test_section_listing_shows_only_own_courses(self):
        response = self.as_(self.professor).get('/section/eso/courses/')

        self.assertContains(response, f'/class/{self.course.pk}/dashboard/')
        self.assertNotContains(
            response, f'/class/{self.other_course.pk}/dashboard/')


class GradeFormIntegrityTests(AccessControlTestCase):
    """The form is the only thing standing between a POST and the Grade table."""

    def _payload(self, **overrides):
        payload = {
            'student': self.student.pk,
            'school_year': self.year.pk,
            'trimester': self.trimester.pk,
            'subject': self.subject.pk,
            'grade_type': 'examen',
            'grade_type_number': 1,
            'grade': '7.50',
            'comments': '',
        }
        payload.update(overrides)
        return payload

    def test_student_is_taken_from_the_url_not_the_body(self):
        """A hidden input is not an authorization boundary."""
        self.as_(self.professor).post(
            f'/student/{self.student.pk}/grade/new/',
            self._payload(student=self.other_student.pk))

        self.assertFalse(Grade.objects.filter(student=self.other_student).exists())
        self.assertTrue(Grade.objects.filter(student=self.student).exists())

    def test_trimester_must_belong_to_the_selected_school_year(self):
        other_year = School_year.objects.create(year='2030-2031')
        foreign_trimester = Trimester.objects.create(
            Name=2, school_year=other_year)

        self.as_(self.professor).post(
            f'/student/{self.student.pk}/grade/new/',
            self._payload(trimester=foreign_trimester.pk))

        self.assertFalse(
            Grade.objects.filter(trimester=foreign_trimester).exists())

    def test_out_of_range_grade_is_rejected(self):
        for value in ('11', '-1'):
            with self.subTest(grade=value):
                self.as_(self.professor).post(
                    f'/student/{self.student.pk}/grade/new/',
                    self._payload(grade=value, grade_type_number=99))
                self.assertFalse(
                    Grade.objects.filter(grade_type_number=99).exists())

    def test_valid_grade_is_still_saved(self):
        """Guard against over-tightening: the happy path must survive."""
        self.as_(self.professor).post(
            f'/student/{self.student.pk}/grade/new/', self._payload())

        self.assertTrue(
            Grade.objects.filter(student=self.student, grade=7.50).exists())


class CsvImportTests(AccessControlTestCase):
    """The import writes straight to the Grade table, so it validates or it lies."""

    HEADER = ('Nombre_Estudiante,Asignatura,Trimestre,Año_Escolar,'
              'Nota,Tipo_Nota,Numero_Tipo_Nota,Comentarios')

    def upload(self, *rows, user=None):
        body = '\n'.join([self.HEADER, *rows]).encode('utf-8')
        upload = SimpleUploadedFile('grades.csv', body, content_type='text/csv')
        return self.as_(user or self.professor).post(
            '/import/grades/', {'csv_file': upload}, follow=True)

    def row(self, student=None, grade='7.5', gtype='examen', number='1',
            trimester='1', year='2025-2026'):
        name = (student or self.student).Name
        return (f'{name},{self.subject.Name},{trimester},{year},'
                f'{grade},{gtype},{number},')

    def test_valid_row_imports(self):
        self.upload(self.row())
        self.assertTrue(Grade.objects.filter(student=self.student).exists())

    def test_blank_grade_is_not_imported_as_zero(self):
        """The template ships blank Nota cells; they used to become 0.0."""
        self.upload(self.row(grade=''))
        self.assertFalse(Grade.objects.exists())

    def test_out_of_range_grade_is_rejected(self):
        for value in ('11', '-5', '99'):
            with self.subTest(grade=value):
                self.upload(self.row(grade=value))
                self.assertFalse(Grade.objects.exists())

    def test_invalid_grade_type_is_rejected(self):
        self.upload(self.row(gtype='no-such-type'))
        self.assertFalse(Grade.objects.exists())

    def test_padded_student_name_still_matches(self):
        """`a or b.strip()` stripped only the fallback, so padding broke lookup."""
        padded = f'  {self.student.Name}  ,{self.subject.Name},1,2025-2026,6,examen,1,'
        self.upload(padded)
        self.assertTrue(Grade.objects.filter(student=self.student).exists())

    def test_spanish_decimal_comma_is_accepted(self):
        """Quoted, as a real es-ES spreadsheet export writes it."""
        self.upload(self.row(grade='"7,5"'))
        self.assertEqual(float(Grade.objects.get().grade), 7.5)

    def test_cannot_grade_a_student_outside_own_classes(self):
        """course_id was fetched and then never used in the loop."""
        self.upload(self.row(student=self.other_student))
        self.assertFalse(Grade.objects.filter(student=self.other_student).exists())

    def test_import_cannot_invent_school_years(self):
        before = School_year.objects.count()
        self.upload(self.row(year='9999-0000'))
        self.assertEqual(School_year.objects.count(), before)

    def test_import_cannot_invent_trimesters(self):
        before = Trimester.objects.count()
        self.upload(self.row(trimester='99'))
        self.assertEqual(Trimester.objects.count(), before)

    def test_oversized_upload_is_refused(self):
        body = b'x' * (MAX_IMPORT_BYTES + 1)
        upload = SimpleUploadedFile('big.csv', body, content_type='text/csv')
        response = self.as_(self.professor).post(
            '/import/grades/', {'csv_file': upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Grade.objects.exists())

    def test_errors_do_not_echo_student_names(self):
        """Import errors are shown in the browser; they carried PII verbatim."""
        response = self.upload(
            'Nombre Inventado,Mates,1,2025-2026,6,examen,1,')
        self.assertNotContains(response, 'Nombre Inventado')


class CsvRoundTripTests(AccessControlTestCase):
    """The template `download_class_list` hands out must import back in.

    It did not. The template's `Año_Escolar` came from `timezone.now()` while
    `import_grades` looks years up and deliberately does not create them, so
    every row failed with "año escolar no encontrado" whenever the calendar
    year and the school year disagreed — which is most of any school year.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A year that cannot coincide with the calendar year, so this keeps
        # catching the old code whenever it is run.
        cls.old_year = School_year.objects.create(year='2019-2020')
        cls.old_trimester = Trimester.objects.create(
            Name=1, school_year=cls.old_year)
        cls.old_course = Course.objects.create(
            Tipo='Eso', Section='3C', school_year=cls.old_year)
        Students_Courses.objects.create(
            student=cls.student, course_section=cls.old_course)
        Subjects_Courses.objects.create(
            subject=cls.subject, teacher=cls.teacher_a, course=cls.old_course,
            trimester=cls.old_trimester)

    def template(self, course):
        """Download the import template and return (fieldnames, rows)."""
        response = self.as_(self.professor).get(
            f'/download/class-list/{course.CourseID}/')
        self.assertEqual(response.status_code, 200)

        reader = csv.DictReader(io.StringIO(response.content.decode('utf-8')))
        return reader.fieldnames, list(reader)

    def reupload(self, course, fieldnames, rows):
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

        upload = SimpleUploadedFile(
            'grades.csv', out.getvalue().encode('utf-8'),
            content_type='text/csv')
        return self.as_(self.professor).post(
            f'/import/grades/{course.CourseID}/', {'csv_file': upload},
            follow=True)

    def test_template_year_comes_from_the_course(self):
        _, rows = self.template(self.old_course)

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row['Año_Escolar'], self.old_year.year)

    def test_template_year_is_not_derived_from_the_calendar(self):
        calendar_year = timezone.now().year
        _, rows = self.template(self.old_course)

        self.assertNotIn(
            f'{calendar_year}-{calendar_year + 1}',
            [row['Año_Escolar'] for row in rows])

    def test_template_headers_are_the_ones_the_importer_reads(self):
        fieldnames, _ = self.template(self.old_course)

        self.assertEqual(fieldnames, [
            'Nombre_Estudiante', 'Asignatura', 'Trimestre', 'Año_Escolar',
            'Nota', 'Tipo_Nota', 'Numero_Tipo_Nota', 'Comentarios'])

    def test_the_downloaded_template_imports_back_in(self):
        """The whole point: fill in the blanks the teacher is meant to fill in."""
        fieldnames, rows = self.template(self.old_course)
        for row in rows:
            row['Asignatura'] = self.subject.Name
            row['Trimestre'] = str(self.old_trimester.Name)
            row['Nota'] = '7.5'

        self.reupload(self.old_course, fieldnames, rows)

        grade = Grade.objects.get(student=self.student)
        self.assertEqual(grade.school_year, self.old_year)
        self.assertEqual(grade.trimester, self.old_trimester)
        self.assertEqual(float(grade.grade), 7.5)

    def test_the_template_no_longer_reports_a_missing_year(self):
        fieldnames, rows = self.template(self.old_course)
        for row in rows:
            row['Asignatura'] = self.subject.Name
            row['Trimestre'] = str(self.old_trimester.Name)
            row['Nota'] = '7.5'

        response = self.reupload(self.old_course, fieldnames, rows)

        self.assertNotContains(response, 'año escolar')

    def test_a_genuinely_missing_year_is_named_in_the_error(self):
        """Years are not PII, and a bare 'no encontrado' is unactionable."""
        fieldnames, rows = self.template(self.old_course)
        for row in rows:
            row['Asignatura'] = self.subject.Name
            row['Trimestre'] = str(self.old_trimester.Name)
            row['Nota'] = '7.5'
            row['Año_Escolar'] = '9999-0000'

        response = self.reupload(self.old_course, fieldnames, rows)

        self.assertContains(response, '9999-0000')
        self.assertFalse(Grade.objects.exists())

    def test_the_importer_still_refuses_to_create_the_year(self):
        """Re-asserted here: the fix is at the producer, not the consumer."""
        before = School_year.objects.count()
        fieldnames, rows = self.template(self.old_course)
        for row in rows:
            row['Asignatura'] = self.subject.Name
            row['Trimestre'] = str(self.old_trimester.Name)
            row['Nota'] = '7.5'
            row['Año_Escolar'] = '9999-0000'

        self.reupload(self.old_course, fieldnames, rows)

        self.assertEqual(School_year.objects.count(), before)


class CsvImportCorrectnessTests(AccessControlTestCase):
    """What a green import hides.

    `CsvImportTests` above pins that the importer validates, and it does. But
    every grade it uploads is 7.5, 6 or 7,5 — and those are binary-exact, the
    one set of values the float conversion below happened not to corrupt. Four
    grades in five could not be imported at all and 307 tests stayed green.

    The rest are the same shape: faults on the paths a successful upload never
    walks — the second half of a file that fails to decode, a re-uploaded
    template, a header that is not the one the importer reads.
    """

    HEADER = ('Nombre_Estudiante,Asignatura,Trimestre,Año_Escolar,'
              'Nota,Tipo_Nota,Numero_Tipo_Nota,Comentarios')

    def url(self, scoped=False):
        return (f'/import/grades/{self.course.CourseID}/' if scoped
                else '/import/grades/')

    def row(self, grade='7.7', student=None, subject=None, trimester='1',
            year='2025-2026', gtype='examen', number='1', comments=''):
        name = (student or self.student).Name
        return (f'{name},{subject or self.subject.Name},{trimester},{year},'
                f'{grade},{gtype},{number},{comments}')

    def upload(self, *rows, scoped=False, body=None, header=None):
        """POST a CSV and return the page. Never `follow=True`: the result is
        rendered in place, and following would hide a regression to a
        redirect."""
        if body is None:
            body = '\n'.join([header or self.HEADER, *rows]).encode('utf-8')
        upload = SimpleUploadedFile(
            'grades.csv', body, content_type='text/csv')
        return self.as_(self.professor).post(
            self.url(scoped), {'csv_file': upload})

    def a_grade(self, value='4.00', comments='', number=1):
        return Grade.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            grade=Decimal(value), grade_type='examen',
            grade_type_number=number, comments=comments)

    # --- 1. the blocker: float -> DecimalField ------------------------------

    def test_a_grade_that_is_not_binary_exact_still_imports(self):
        """`grade.grade = float(...)` on a DecimalField(decimal_places=2).

        Django converts an assigned float with `create_decimal_from_float()`,
        so float('7.7') arrives as a Decimal with three decimal places and
        `DecimalValidator` rejects it. Of the 101 one-decimal grades from 0.0
        to 10.0 exactly 21 imported — the multiples of 0.5.
        """
        for number, value in enumerate(('7.7', '2.66', '8.3', '9.99'), 1):
            with self.subTest(grade=value):
                Grade.objects.all().delete()
                self.upload(self.row(grade=value, number=str(number)))

                self.assertEqual(Grade.objects.get().grade, Decimal(value))

    def test_the_only_grade_in_the_live_database_can_be_imported(self):
        """2.66 — and therefore its own export, re-headered. Quoted, as a real
        es-ES spreadsheet writes it."""
        response = self.upload(self.row(grade='"2,66"'))

        self.assertEqual(Grade.objects.get().grade, Decimal('2.66'))
        self.assertEqual(response.context['result']['error_count'], 0)

    def test_the_management_command_imports_the_same_values(self):
        """The identical line lived in the CLI importer too."""
        import tempfile
        from django.core.management import call_command

        with tempfile.NamedTemporaryFile(
                'w', suffix='.csv', encoding='utf-8', delete=False,
                newline='') as handle:
            handle.write(f'{self.HEADER}\n{self.row(grade="7.7")}\n')
            path = handle.name

        call_command('import_grades', path)

        self.assertEqual(Grade.objects.get().grade, Decimal('7.7'))

    # --- 2. a decode error mid-file ----------------------------------------

    def test_rows_committed_before_a_decode_error_are_still_reported(self):
        """Two rows land, the third fails to decode, and the summary used to
        be discarded — so the page said only "no se ha podido leer" while two
        grades sat in the database."""
        lines = [self.HEADER.encode('utf-8'),
                 self.row(grade='7.5', number='1').encode('utf-8'),
                 self.row(grade='6.5', number='2').encode('utf-8'),
                 # A latin-1 accented byte: not valid UTF-8, so iterdecode
                 # raises on this line and not before it.
                 'Ana Lópe'.encode('latin-1') + b',x,1,2025-2026,5,examen,3,']
        response = self.upload(body=b'\n'.join(lines))

        self.assertEqual(Grade.objects.count(), 2)
        self.assertIsNotNone(response.context['result'])
        self.assertEqual(response.context['result']['created'], 2)
        self.assertContains(response, 'Filas leídas')

    # --- 3. the Excel BOM ---------------------------------------------------

    def test_a_utf8_bom_does_not_turn_into_a_missing_student(self):
        """Excel's default "CSV UTF-8" writes one. Decoded as plain utf-8 it
        becomes part of the first header name, the student column is never
        read, and every row fails blaming the roster."""
        body = ('﻿' + self.HEADER + '\n'
                + self.row(grade='7.5')).encode('utf-8')
        response = self.upload(body=body)

        self.assertTrue(Grade.objects.exists())
        self.assertNotContains(response, 'Alumno no encontrado')

    # --- 4. the comment wipe ------------------------------------------------

    def test_a_blank_comment_column_does_not_erase_the_comment(self):
        """`download_class_list` always ships `Comentarios` blank, so the
        documented workflow — download, fill in one grade, upload — erased the
        comment on every grade it touched."""
        grade = self.a_grade(comments='Muy buen trimestre')
        self.upload(self.row(grade='8.5', comments=''))

        grade.refresh_from_db()
        self.assertEqual(grade.comments, 'Muy buen trimestre')
        self.assertEqual(grade.grade, Decimal('8.5'))

    def test_a_comment_the_teacher_typed_still_overwrites(self):
        """Blank means "not stated", not "keep whatever is there whatever I
        write" — the fix must not make comments unwritable."""
        grade = self.a_grade(comments='Muy buen trimestre')
        self.upload(self.row(grade='8.5', comments='Ha mejorado'))

        grade.refresh_from_db()
        self.assertEqual(grade.comments, 'Ha mejorado')

    # --- 5. the unscoped subject lookup -------------------------------------

    def test_a_subject_the_teacher_does_not_teach_is_refused(self):
        """The student lookup was scoped and this one was not, so any subject
        in the catalogue could be graded as long as the student was yours."""
        foreign = Subjects.objects.create(Name='Fisica')
        response = self.upload(self.row(grade='7.5', subject='Fisica'))

        self.assertFalse(Grade.objects.filter(subject=foreign).exists())
        self.assertContains(response, 'Fisica')

    def test_the_teachers_own_subject_is_still_importable(self):
        """Guards the fix against over-reaching: scoping must not refuse the
        subject the teacher is actually assigned to."""
        self.upload(self.row(grade='7.5'), scoped=True)

        self.assertTrue(Grade.objects.filter(subject=self.subject).exists())

    # --- 6. one message for four causes -------------------------------------

    def test_each_invalid_value_is_reported_as_itself(self):
        """Out of range, unknown type, too many decimals and a negative number
        all rendered the same sentence, and Django's own message — which had
        already said "no más de 2 decimales" — was discarded. That is why the
        float bug above was invisible."""
        cases = (
            ('demasiados decimales', self.row(grade='7.777'), 'Nota:'),
            ('fuera de rango', self.row(grade='11'), 'Nota:'),
            ('tipo inexistente', self.row(gtype='inventado'), 'Tipo_Nota:'),
            ('numero negativo', self.row(number='-1'),
             'Numero_Tipo_Nota:'),
        )
        for label, row, column in cases:
            with self.subTest(case=label):
                response = self.upload(row)

                self.assertFalse(Grade.objects.exists())
                self.assertContains(response, column)
                self.assertNotContains(response, 'Valores no válidos')

    # --- 7. the year the file names vs the year the course is in ------------

    def test_a_class_scoped_import_refuses_another_years_row(self):
        """`Course.school_year` fixes the year, exactly as it does for
        `resolve_class_scope`. A row naming a different *existing* year was
        written into that other year without a word."""
        other = School_year.objects.create(year='2024-2025')
        Trimester.objects.create(Name=1, school_year=other)

        response = self.upload(
            self.row(grade='7.5', year='2024-2025'), scoped=True)

        self.assertFalse(Grade.objects.exists())
        self.assertContains(response, '2024-2025')
        self.assertContains(response, '2025-2026')

    def test_the_unscoped_route_still_accepts_any_year_it_knows(self):
        """There is no course to disagree with, so the check must not fire."""
        other = School_year.objects.create(year='2024-2025')
        Trimester.objects.create(Name=1, school_year=other)

        self.upload(self.row(grade='7.5', year='2024-2025'))

        self.assertTrue(Grade.objects.filter(school_year=other).exists())

    # --- 8 and 10. what the counters count, and what the cap hides ----------

    def test_the_row_limit_notice_survives_the_error_display_cap(self):
        """It was appended to `errors` last and `errors` is sliced to 50, so
        on the only kind of file that can trigger it the notice was the first
        thing hidden. And "Filas leídas" counted it as a row."""
        from unittest.mock import patch

        rows = [f'Fantasma {n},{self.subject.Name},1,2025-2026,7.5,examen,{n},'
                for n in range(52)]
        with patch('mainapp.views.MAX_IMPORT_ROWS', 51):
            response = self.upload(*rows)

        self.assertContains(response, 'límite de 51 filas')
        self.assertEqual(response.context['result']['rows'], 51)

    def test_an_empty_file_is_refused_rather_than_reported_as_success(self):
        response = self.upload(body=b'')

        self.assertContains(response, 'vacío')
        self.assertNotContains(response, 'Filas leídas')

    def test_a_headers_only_file_says_there_was_nothing_to_import(self):
        """0 / 0 / 0 and no message reads as a successful import."""
        response = self.upload()

        self.assertContains(response, 'solo tiene la cabecera')
        self.assertNotContains(response, 'Filas leídas')

    # --- 9. structural faults are faults of the file ------------------------

    def test_an_export_reuploaded_unchanged_is_refused_as_a_whole(self):
        """This is what makes re-importing an export say "Alumno no
        encontrado": `Estudiante` is not `Nombre_Estudiante`, so every cell
        reads empty and the rows fail one by one for the wrong reason."""
        header = ('Estudiante,Asignatura,Trimestre,Año Escolar,Nota,'
                  'Tipo de Nota,Numero,Comentario')
        response = self.upload(
            f'{self.student.Name},{self.subject.Name},1,2025-2026,7.5,'
            f'examen,1,', header=header)

        self.assertContains(response, 'Faltan columnas en la cabecera')
        self.assertNotContains(response, 'Alumno no encontrado')
        self.assertNotContains(response, 'Filas leídas')

    def test_a_semicolon_separated_file_is_refused_as_a_whole(self):
        body = (self.HEADER.replace(',', ';') + '\n'
                + self.row(grade='7.5').replace(',', ';')).encode('utf-8')
        response = self.upload(body=body)

        self.assertContains(response, 'Faltan columnas en la cabecera')
        self.assertNotContains(response, 'Alumno no encontrado')

    def test_a_missing_column_is_named_rather_than_inferred(self):
        header = self.HEADER.replace(',Nota,', ',')
        response = self.upload(
            f'{self.student.Name},{self.subject.Name},1,2025-2026,'
            f'examen,1,', header=header)

        self.assertContains(response, 'Faltan columnas en la cabecera')
        self.assertContains(response, 'Nota')

    def test_a_short_row_says_its_columns_do_not_match(self):
        """It used to be reported as whichever cell happened to end up empty
        — "Falta la nota" for a row that is missing five columns."""
        response = self.upload(f'{self.student.Name},{self.subject.Name}')

        self.assertContains(response, 'menos columnas')
        self.assertNotContains(response, 'Falta la nota')

    # --- the upsert, made visible -------------------------------------------

    def test_an_overwritten_grade_is_named_with_its_old_value(self):
        """Import is an upsert and stays one — whether a file may replace an
        existing grade is the user's call. But "actualizadas: 1" does not say
        what was replaced, and the old value is gone by the time it is read.
        """
        self.a_grade(value='4.00')
        response = self.upload(self.row(grade='8.5'))

        updates = response.context['result']['updates']
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['old'], Decimal('4.00'))
        self.assertEqual(updates[0]['new'], Decimal('8.5'))
        self.assertEqual(updates[0]['student'], self.student.Name)
        self.assertContains(response, 'Notas sustituidas')

    def test_an_import_that_creates_shows_no_overwrite_table(self):
        """An empty "Notas sustituidas" heading claims an overwrite."""
        response = self.upload(self.row(grade='8.5'))

        self.assertContains(response, 'Notas creadas')
        self.assertNotContains(response, 'Notas sustituidas')


class ClassDashboardLinkTests(AccessControlTestCase):
    """Links into `class_dashboard` must carry scope it reads, or nothing.

    `resolve_class_scope` reads `trimester_id` and `subject_courses_id` and
    ignores `school_year_id` — `Course.school_year` fixes the year. A link
    appending the ignored param reads as a filter that does not exist.
    """

    def hrefs(self, html, course):
        """Every href in `html` pointing at this course's class dashboard."""
        needle = f'/class/{course.CourseID}/dashboard/'
        return [chunk.split('"')[0]
                for chunk in html.split(f'href="{needle}')[1:]]

    def test_section_listing_does_not_append_the_ignored_year(self):
        response = self.as_(self.professor).get('/section/eso/courses/')
        html = response.content.decode('utf-8')

        links = self.hrefs(html, self.course)
        self.assertTrue(links)
        for link in links:
            self.assertNotIn('school_year_id', link)

    def test_teacher_dashboard_does_not_append_the_ignored_year(self):
        response = self.as_(self.professor).get('/teacher/')
        html = response.content.decode('utf-8')

        links = self.hrefs(html, self.course)
        self.assertTrue(links)
        for link in links:
            self.assertNotIn('school_year_id', link)

    def test_a_listing_link_lands_on_the_courses_own_year(self):
        """The dropped param was redundant as well as ignored."""
        response = self.as_(self.professor).get(
            f'/class/{self.course.CourseID}/dashboard/')

        self.assertEqual(
            response.context['scope'].school_year, self.course.school_year)


class UnlinkedTeacherTests(AccessControlTestCase):
    """Scoping must fail closed: no Teachers link means no data, not all data."""

    def test_unlinked_professor_is_refused(self):
        for url in ['/teacher/', '/search/?q=a', '/import/grades/',
                    f'/class/{self.course.pk}/dashboard/']:
            with self.subTest(url=url):
                self.assertEqual(
                    self.as_(self.unlinked_professor).get(url).status_code, 403)

    def test_unlinked_professor_csv_export_is_empty(self):
        """grades_csv branches per role, so it needs its own assertion."""
        body = self.as_(self.unlinked_professor).get('/grades/csv/').content

        self.assertNotIn(self.student.Name.encode(), body)
        self.assertNotIn(self.other_student.Name.encode(), body)


class ClassScopeTests(AccessControlTestCase):
    """Scope resolution for class pages: year, trimester, subject, roster.

    The roster rule is a hybrid, and its two halves fail in opposite
    directions: course enrolment alone cannot express optativas, while
    `assigned_course_sections` alone is empty on most rows and has no defined
    answer before a subject is chosen. These tests pin the handover between
    them, and pin that the subject roster can only ever narrow.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A second trimester in the same year, with its own subject row: the
        # subject set is per trimester, so T1 and T2 must not bleed together.
        cls.trimester_2 = Trimester.objects.create(
            Name=2, school_year=cls.year)
        cls.optativa = Subjects.objects.create(Name='Latin')
        cls.sc_t1 = Subjects_Courses.objects.get(
            course=cls.course, trimester=cls.trimester)
        cls.sc_t2 = Subjects_Courses.objects.create(
            subject=cls.optativa, teacher=cls.teacher_a, course=cls.course,
            trimester=cls.trimester_2)
        # Another course's row, to test that a foreign id is not honoured.
        cls.sc_other_course = Subjects_Courses.objects.get(
            course=cls.other_course, trimester=cls.trimester)

        # A second student enrolled in cls.course, so a subject roster has
        # something to narrow away.
        cls.student_c = Students.objects.create(
            Name='Christian Gonzalez', Email='christian@example.com')
        cls.enrolment_c = Students_Courses.objects.create(
            student=cls.student_c, course_section=cls.course)

    def scope(self, **kwargs):
        return resolve_class_scope(self.course, **kwargs)

    # --- year and trimester -------------------------------------------------

    def test_school_year_comes_from_the_course_not_the_query(self):
        other_year = School_year.objects.create(year='2099-2100')

        scope = self.scope()

        self.assertEqual(scope.school_year, self.course.school_year)
        self.assertNotEqual(scope.school_year, other_year)

    def test_defaults_to_the_first_trimester(self):
        self.assertEqual(self.scope().trimester, self.trimester)

    def test_honours_a_valid_trimester_id(self):
        self.assertEqual(
            self.scope(trimester_id=self.trimester_2.pk).trimester,
            self.trimester_2)

    def test_unknown_trimester_id_falls_back_instead_of_raising(self):
        """Stale bookmarks are routine; a 404 would be a worse page."""
        for bad in ['999999', 'abc', '']:
            with self.subTest(trimester_id=bad):
                self.assertEqual(
                    self.scope(trimester_id=bad).trimester, self.trimester)

    def test_trimester_from_another_year_is_not_honoured(self):
        foreign_year = School_year.objects.create(year='2099-2100')
        foreign = Trimester.objects.create(Name=1, school_year=foreign_year)

        self.assertEqual(
            self.scope(trimester_id=foreign.pk).trimester, self.trimester)

    def test_year_with_no_trimesters_yields_no_scope_but_still_a_roster(self):
        Trimester.objects.filter(school_year=self.year).delete()

        scope = self.scope()

        self.assertIsNone(scope.trimester)
        self.assertEqual(scope.subjects_courses, [])
        self.assertIn(self.student, scope.students)

    # --- subjects -----------------------------------------------------------

    def test_subject_list_is_filtered_by_trimester(self):
        """Subjects_Courses carries a trimester FK; unfiltered it repeats."""
        self.assertEqual(self.scope().subjects_courses, [self.sc_t1])
        self.assertEqual(
            self.scope(trimester_id=self.trimester_2.pk).subjects_courses,
            [self.sc_t2])

    def test_no_subject_is_selected_by_default(self):
        scope = self.scope()

        self.assertIsNone(scope.subject_courses)
        self.assertIsNone(scope.subject)

    def test_subject_from_another_course_is_not_selected(self):
        scope = self.scope(subject_courses_id=self.sc_other_course.pk)

        self.assertIsNone(scope.subject_courses)

    def test_subject_from_another_trimester_is_not_selected(self):
        scope = self.scope(subject_courses_id=self.sc_t2.pk)

        self.assertIsNone(scope.subject_courses)

    # --- the hybrid roster --------------------------------------------------

    def test_roster_defaults_to_course_enrolment(self):
        scope = self.scope()

        self.assertEqual(scope.roster_source, 'course')
        self.assertEqual(
            list(scope.students), [self.student, self.student_c])

    def test_roster_falls_back_when_the_subject_has_no_list_of_its_own(self):
        """The M2M is empty on most rows, so the fallback is the common path."""
        self.assertFalse(self.sc_t1.assigned_course_sections.exists())

        scope = self.scope(subject_courses_id=self.sc_t1.pk)

        self.assertEqual(scope.subject_courses, self.sc_t1)
        self.assertEqual(scope.roster_source, 'course')
        self.assertEqual(
            list(scope.students), [self.student, self.student_c])

    def test_roster_narrows_when_the_subject_has_its_own_list(self):
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        scope = self.scope(subject_courses_id=self.sc_t1.pk)

        self.assertEqual(scope.roster_source, 'subject')
        self.assertEqual(list(scope.students), [self.student])

    def test_subject_roster_can_never_widen_past_the_course(self):
        """Nothing constrains the M2M to enrolments in the owning course."""
        foreign_enrolment = Students_Courses.objects.get(
            student=self.other_student, course_section=self.other_course)
        self.sc_t1.assigned_course_sections.set(
            [self.enrolment, foreign_enrolment])

        scope = self.scope(subject_courses_id=self.sc_t1.pk)

        self.assertEqual(list(scope.students), [self.student])
        self.assertNotIn(self.other_student, scope.students)

    def test_roster_is_ordered_by_name_and_deduplicated(self):
        # A second enrolment for the same student in another section is the
        # shape that produces duplicate rows without .distinct().
        Students_Courses.objects.create(
            student=self.student, course_section=self.other_course)

        names = [s.Name for s in self.scope().students]

        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(set(names)))

    # --- scope round-tripping ----------------------------------------------

    def test_query_string_carries_the_selected_scope(self):
        self.sc_t1.assigned_course_sections.set([self.enrolment])
        scope = self.scope(trimester_id=self.trimester.pk,
                           subject_courses_id=self.sc_t1.pk)

        self.assertEqual(
            scope.query_params,
            {'trimester_id': self.trimester.pk,
             'subject_courses_id': self.sc_t1.pk})
        self.assertIn(f'trimester_id={self.trimester.pk}', scope.query_string)

    def test_dashboard_ignores_school_year_id_from_section_courses_links(self):
        """No in-app link appends it any more, but bookmarks still carry it."""
        response = self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/?school_year_id=999999')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['scope'].school_year, self.course.school_year)

    def test_dashboard_exposes_the_resolved_scope(self):
        response = self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/'
            f'?trimester_id={self.trimester_2.pk}')

        scope = response.context['scope']
        self.assertEqual(scope.trimester, self.trimester_2)
        self.assertEqual(scope.subjects_courses, [self.sc_t2])

    def test_absence_post_redirects_back_into_the_same_scope(self):
        response = self.as_(self.professor).post(
            f'/class/{self.course.pk}/dashboard/'
            f'?trimester_id={self.trimester.pk}',
            {'students': [self.student.pk], 'subject': self.subject.pk,
             'school_year': self.year.pk, 'trimester': self.trimester.pk,
             'Tipo': 'Ausencia'})

        self.assertEqual(response.status_code, 302)
        self.assertIn(f'trimester_id={self.trimester.pk}', response['Location'])


class ClassMetricsTests(AccessControlTestCase):
    """Aggregates for a class page. This app had none before, so every figure
    here is new backend behaviour rather than a display change.

    The two claims most easily got wrong, and both are pinned below: a grade
    count has no denominator, and the class mean is weighted by grade count
    rather than being an average of per-student averages.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.trimester_2 = Trimester.objects.create(
            Name=2, school_year=cls.year)
        cls.other_year = School_year.objects.create(year='2024-2025')
        cls.other_year_trimester = Trimester.objects.create(
            Name=1, school_year=cls.other_year)
        # A subject nobody teaches to this course: grades in it must not count.
        cls.foreign_subject = Subjects.objects.create(Name='Filosofia')

        cls.student_c = Students.objects.create(
            Name='Christian Gonzalez', Email='christian@example.com')
        cls.enrolment_c = Students_Courses.objects.create(
            student=cls.student_c, course_section=cls.course)

        cls.sc_t1 = Subjects_Courses.objects.get(
            course=cls.course, trimester=cls.trimester)

    def grade(self, student, value, subject=None, trimester=None,
              school_year=None, grade_type='examen', number=0):
        return Grade.objects.create(
            student=student, subject=subject or self.subject,
            trimester=trimester or self.trimester,
            school_year=school_year or self.year,
            grade=Decimal(value), grade_type=grade_type,
            grade_type_number=number)

    def absence(self, student, tipo='Ausencia', when=None, subject=None):
        return Ausencias.objects.create(
            student=student, subject=subject or self.subject,
            trimester=self.trimester, school_year=self.year, Tipo=tipo,
            date_time=when or timezone.now())

    def metrics(self, **kwargs):
        return class_metrics(resolve_class_scope(self.course, **kwargs))

    def row_for(self, metrics, student):
        return next(r for r in metrics.rows if r.student == student)

    # --- per student --------------------------------------------------------

    def test_row_exists_for_every_student_in_the_roster(self):
        metrics = self.metrics()

        self.assertEqual([r.student for r in metrics.rows],
                         [self.student, self.student_c])
        self.assertEqual(metrics.enrolled, 2)

    def test_ungraded_student_has_no_mean_rather_than_a_zero(self):
        """Zero is a mark. 'Not yet graded' is not one."""
        row = self.row_for(self.metrics(), self.student)

        self.assertEqual(row.grade_count, 0)
        self.assertIsNone(row.mean)
        self.assertFalse(row.evaluated)

    def test_counts_and_averages_a_students_grades(self):
        self.grade(self.student, '5.00', number=1)
        self.grade(self.student, '8.00', number=2)

        row = self.row_for(self.metrics(), self.student)

        self.assertEqual(row.grade_count, 2)
        self.assertEqual(row.mean, Decimal('6.50'))
        self.assertTrue(row.evaluated)

    def test_grade_count_is_unbounded_and_carries_no_denominator(self):
        """Grade is unique per (…, grade_type, grade_type_number), so a single
        student can hold far more grades than there are grade types."""
        for n in range(1, 8):
            self.grade(self.student, '7.00', number=n)

        self.assertEqual(
            self.row_for(self.metrics(), self.student).grade_count, 7)

    def test_all_grade_types_are_pooled(self):
        self.grade(self.student, '4.00', grade_type='examen')
        self.grade(self.student, '10.00', grade_type='trimestral')

        self.assertEqual(
            self.row_for(self.metrics(), self.student).mean, Decimal('7.00'))

    def test_mean_is_rounded_to_two_places(self):
        self.grade(self.student, '5.00', number=1)
        self.grade(self.student, '5.00', number=2)
        self.grade(self.student, '6.00', number=3)

        self.assertEqual(
            self.row_for(self.metrics(), self.student).mean, Decimal('5.33'))

    # --- what is out of scope ----------------------------------------------

    def test_grades_from_another_trimester_are_excluded(self):
        self.grade(self.student, '9.00', trimester=self.trimester_2)

        self.assertEqual(
            self.row_for(self.metrics(), self.student).grade_count, 0)

    def test_grades_from_another_school_year_are_excluded(self):
        self.grade(self.student, '9.00',
                   trimester=self.other_year_trimester,
                   school_year=self.other_year)

        self.assertEqual(
            self.row_for(self.metrics(), self.student).grade_count, 0)

    def test_grades_in_a_subject_not_taught_to_this_course_are_excluded(self):
        """Grade has no FK to Course, so the subject set is what bounds it."""
        self.grade(self.student, '9.00', subject=self.foreign_subject)

        self.assertEqual(
            self.row_for(self.metrics(), self.student).grade_count, 0)

    def test_selecting_a_subject_narrows_the_figures_to_it(self):
        second = Subjects.objects.create(Name='Lengua')
        Subjects_Courses.objects.create(
            subject=second, teacher=self.teacher_a, course=self.course,
            trimester=self.trimester)
        self.grade(self.student, '4.00')
        self.grade(self.student, '10.00', subject=second)

        both = self.row_for(self.metrics(), self.student)
        only_maths = self.row_for(
            self.metrics(subject_courses_id=self.sc_t1.pk), self.student)

        self.assertEqual(both.grade_count, 2)
        self.assertEqual(only_maths.grade_count, 1)
        self.assertEqual(only_maths.mean, Decimal('4.00'))

    def test_a_narrowed_roster_narrows_the_totals_too(self):
        self.sc_t1.assigned_course_sections.set([self.enrolment])
        self.grade(self.student, '4.00')
        self.grade(self.student_c, '10.00')

        metrics = self.metrics(subject_courses_id=self.sc_t1.pk)

        self.assertEqual(metrics.enrolled, 1)
        self.assertEqual(metrics.grade_count, 1)
        self.assertEqual(metrics.mean, Decimal('4.00'))

    # --- the class strip ----------------------------------------------------

    def test_class_mean_is_weighted_by_grade_count(self):
        """The whole point of the weighting: a mean of means is a different
        number whenever students hold different numbers of grades."""
        self.grade(self.student, '10.00', number=1)
        self.grade(self.student, '10.00', number=2)
        self.grade(self.student, '10.00', number=3)
        self.grade(self.student_c, '2.00')

        metrics = self.metrics()

        # weighted: (10+10+10+2)/4 = 8.00 — mean of means would be 6.00
        self.assertEqual(metrics.mean, Decimal('8.00'))

    def test_class_mean_is_none_when_nothing_is_graded(self):
        self.assertIsNone(self.metrics().mean)

    def test_evaluated_counts_students_with_at_least_one_grade(self):
        self.grade(self.student, '6.00')

        metrics = self.metrics()

        self.assertEqual(metrics.enrolled, 2)
        self.assertEqual(metrics.evaluated, 1)

    def test_totals_sum_the_rows(self):
        self.grade(self.student, '6.00', number=1)
        self.grade(self.student, '7.00', number=2)
        self.grade(self.student_c, '8.00')

        self.assertEqual(self.metrics().grade_count, 3)

    # --- absences -----------------------------------------------------------

    def test_absences_are_counted_per_student_and_in_total(self):
        now = timezone.now()
        self.absence(self.student, when=now)
        self.absence(self.student, when=now + timedelta(days=1))
        self.absence(self.student_c, when=now)

        metrics = self.metrics()

        self.assertEqual(self.row_for(metrics, self.student).ausencias_count, 2)
        self.assertEqual(metrics.ausencias_count, 3)

    def test_retraso_counts_alongside_ausencia(self):
        """Tipo is not part of the unique key and is not filtered on here."""
        now = timezone.now()
        self.absence(self.student, tipo='Ausencia', when=now)
        self.absence(self.student, tipo='Retraso',
                     when=now + timedelta(hours=1))

        self.assertEqual(
            self.row_for(self.metrics(), self.student).ausencias_count, 2)

    def test_absences_do_not_leak_into_grade_figures(self):
        """The failure mode of annotating both multi-valued relations at once
        is that each inflates the other's count."""
        now = timezone.now()
        self.grade(self.student, '6.00', number=1)
        self.grade(self.student, '8.00', number=2)
        self.absence(self.student, when=now)
        self.absence(self.student, when=now + timedelta(days=1))
        self.absence(self.student, when=now + timedelta(days=2))

        row = self.row_for(self.metrics(), self.student)

        self.assertEqual(row.grade_count, 2)
        self.assertEqual(row.mean, Decimal('7.00'))
        self.assertEqual(row.ausencias_count, 3)

    # --- cost ---------------------------------------------------------------

    def test_query_count_does_not_grow_with_the_roster(self):
        """Asserted equal at 3 and at 30 students, per the plan's budget.

        Five: three to resolve the scope (trimesters, subjects, roster) and two
        to aggregate. Well inside the budget of 14, and flat — the roster is
        passed to both aggregates as an id list, not walked.
        """
        with self.assertNumQueries(5) as small:
            self.metrics()

        for i in range(28):
            extra = Students.objects.create(
                Name=f'Alumno {i:02d}', Email=f'a{i}@example.com')
            Students_Courses.objects.create(
                student=extra, course_section=self.course)
            self.grade(extra, '7.00')

        with self.assertNumQueries(len(small.captured_queries)):
            metrics = self.metrics()

        self.assertEqual(metrics.enrolled, 30)

    def test_dashboard_exposes_the_metrics(self):
        self.grade(self.student, '2.66')

        response = self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/')

        metrics = response.context['metrics']
        self.assertEqual(metrics.grade_count, 1)
        self.assertEqual(metrics.mean, Decimal('2.66'))


class ClassDashboardTemplateTests(AccessControlTestCase):
    """The register page itself. Two roster states reach this template and they
    must never read alike, because one is a real subset and the other is the
    whole group standing in for a subject that has no list."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student_c = Students.objects.create(
            Name='Christian Gonzalez', Email='christian@example.com')
        Students_Courses.objects.create(
            student=cls.student_c, course_section=cls.course)
        cls.sc_t1 = Subjects_Courses.objects.get(
            course=cls.course, trimester=cls.trimester)

    def get(self, query=''):
        return self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/{query}')

    def test_page_is_built_on_the_v2_cascade_only(self):
        """Tailwind's Preflight and the legacy stylesheets collide, which is
        the whole reason base_v2 exists."""
        response = self.get()

        self.assertTemplateUsed(response, 'base_v2.html')
        self.assertContains(response, 'css/tailwind.css')
        for legacy in ('css/navbar.css', 'css/sidebar.css',
                       'css/site-pages.css', 'css/global-styles.css'):
            self.assertNotContains(response, legacy)

    def test_scripts_are_self_hosted(self):
        """CSP is script-src 'self' with no unsafe-inline and no CDN."""
        response = self.get()

        self.assertContains(response, 'js/vendor/htmx')
        self.assertNotContains(response, '//unpkg.com')
        self.assertNotContains(response, '//cdn.')

    def test_a_subject_with_its_own_list_is_labelled_as_a_subset(self):
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        response = self.get(f'?subject_courses_id={self.sc_t1.pk}')

        self.assertContains(response, 'Lista propia')
        self.assertNotContains(response, 'no tiene lista propia')

    def test_a_subject_without_a_list_says_so_rather_than_looking_the_same(self):
        self.assertFalse(self.sc_t1.assigned_course_sections.exists())

        response = self.get(f'?subject_courses_id={self.sc_t1.pk}')

        self.assertContains(response, 'no tiene lista propia')

    def test_grade_count_is_shown_without_a_denominator(self):
        """A "1 de 3" here would assert a cardinality the schema does not have."""
        Grade.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            grade=Decimal('2.66'), grade_type='examen', grade_type_number=0)

        response = self.get()

        self.assertContains(response, 'Notas registradas')
        self.assertNotContains(response, '1 de 3')


class ClassDashboardHtmxTests(AccessControlTestCase):
    """Step 4: changing the trimester or the subject swaps the register in
    place. The links stay real <a href> and the page keeps working with
    JavaScript off, so every assertion here is about the boost being a layer
    on top rather than a replacement."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sc_t1 = Subjects_Courses.objects.get(
            course=cls.course, trimester=cls.trimester)

    def get(self, query='', htmx=False):
        headers = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
        return self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/{query}', **headers)

    def test_a_scope_change_returns_the_fragment_alone(self):
        """A boosted request pays for the register, not for the shell."""
        response = self.get(f'?trimester_id={self.trimester.pk}', htmx=True)

        self.assertTemplateUsed(response, 'mainapp/_class_scope.html')
        self.assertTemplateNotUsed(response, 'base_v2.html')
        self.assertContains(response, 'id="class-scope"')
        self.assertNotContains(response, '<!doctype')
        self.assertNotContains(response, 'css/tailwind.css')

    def test_a_normal_request_still_returns_the_whole_page(self):
        response = self.get()

        self.assertTemplateUsed(response, 'base_v2.html')
        self.assertTemplateUsed(response, 'mainapp/_class_scope.html')
        self.assertContains(response, 'id="class-scope"')
        self.assertContains(response, 'css/tailwind.css')

    def test_the_swap_target_survives_the_swap(self):
        """hx-swap="outerHTML" replaces the target with the response, so the
        fragment has to carry the id it is replacing or the second swap has
        nowhere to land."""
        first = self.get(f'?trimester_id={self.trimester.pk}', htmx=True)

        self.assertContains(first, 'id="class-scope"')
        self.assertContains(first, 'hx-target="#class-scope"')

    def test_the_nav_count_is_swapped_out_of_band(self):
        """metrics.enrolled also appears in the shell's "Esta clase" block,
        outside the fragment; without the out-of-band swap it goes stale the
        moment a subject narrows the roster."""
        fragment = self.get(f'?subject_courses_id={self.sc_t1.pk}', htmx=True)

        self.assertContains(fragment, 'hx-swap-oob="true"')
        self.assertContains(fragment, 'id="class-enrolled"')

    def test_a_full_page_does_not_emit_the_out_of_band_element(self):
        """It would be a second element with id="class-enrolled" — invalid
        HTML, and htmx ignores oob on a full page load anyway."""
        response = self.get()

        self.assertNotContains(response, 'hx-swap-oob')
        self.assertEqual(response.content.count(b'id="class-enrolled"'), 1)

    def test_the_scope_links_are_real_hrefs(self):
        """No JavaScript, no swap, but the filters must still work."""
        response = self.get()

        self.assertContains(response, f'href="?trimester_id={self.trimester.pk}')
        self.assertContains(
            response, f'subject_courses_id={self.sc_t1.pk}')

    def test_the_boost_does_not_reach_the_download_links(self):
        """Boosting the operations bar would AJAX the CSV downloads and swap
        the file into the page."""
        response = self.get()

        body = response.content.decode()
        boost_at = body.index('hx-boost')
        scope_bar_end = body.index('Lista de clase')
        self.assertLess(boost_at, scope_bar_end)
        self.assertEqual(body.count('hx-boost'), 1)

    def test_no_inline_event_attributes_anywhere(self):
        """hx-on: evals strings and violates script-src 'self'."""
        body = self.get().content.decode()

        self.assertNotIn('hx-on', body)
        self.assertNotIn('onclick=', body)
        self.assertNotIn('onchange=', body)

    def test_the_fragment_honours_the_scope_it_was_asked_for(self):
        """The swap is only worth anything if the fragment is actually
        re-scoped, not just re-rendered."""
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        fragment = self.get(f'?subject_courses_id={self.sc_t1.pk}', htmx=True)

        self.assertContains(fragment, 'Lista propia')

    def test_a_post_is_never_answered_with_a_fragment(self):
        """The absence write stays a plain form submit; only GETs are boosted.
        An invalid POST must re-render the whole page, header or no header."""
        response = self.as_(self.professor).post(
            f'/class/{self.course.pk}/dashboard/', {}, HTTP_HX_REQUEST='true')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'base_v2.html')


class AusenciaFormScopeTests(AccessControlTestCase):
    """Step 6: the absence panel is built from the ClassScope, not the Course.

    Before this, the register could show two students under a subject while
    the panel beside it offered three — two different answers to "who is in
    this class" on one screen."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A second student in the same course, so a narrowed roster has
        # somebody to exclude.
        cls.student_b = Students.objects.create(
            Name='Christian Gonzalez', Email='christian@example.com')
        cls.enrolment_b = Students_Courses.objects.create(
            student=cls.student_b, course_section=cls.course)

        cls.sc_t1 = Subjects_Courses.objects.get(
            course=cls.course, trimester=cls.trimester)
        # A subject taught in a different trimester of the same year.
        cls.trimester_2 = Trimester.objects.create(
            Name=2, school_year=cls.year)
        cls.other_subject = Subjects.objects.create(Name='Fisica')
        Subjects_Courses.objects.create(
            subject=cls.other_subject, teacher=cls.teacher_a,
            course=cls.course, trimester=cls.trimester_2)

    def form_for(self, query=''):
        response = self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/{query}')
        return response.context['ausencia_form'], response

    def test_the_student_list_narrows_with_the_subject_roster(self):
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        form, _ = self.form_for(f'?subject_courses_id={self.sc_t1.pk}')

        offered = list(form.fields['students'].queryset)
        self.assertEqual(offered, [self.student])
        self.assertNotIn(self.student_b, offered)

    def test_without_a_subject_list_the_panel_offers_the_whole_group(self):
        self.assertFalse(self.sc_t1.assigned_course_sections.exists())

        form, _ = self.form_for(f'?subject_courses_id={self.sc_t1.pk}')

        self.assertCountEqual(
            list(form.fields['students'].queryset),
            [self.student, self.student_b])

    def test_the_panel_and_the_register_offer_the_same_roster(self):
        """The defect this step closes is the two disagreeing."""
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        form, response = self.form_for(f'?subject_courses_id={self.sc_t1.pk}')

        self.assertEqual(
            list(form.fields['students'].queryset),
            [row.student for row in response.context['metrics'].rows])

    def test_the_subject_choices_are_this_trimester_only(self):
        """Subjects_Courses carries a trimester FK, so Fisica in T2 is not on
        offer while the page is showing T1."""
        form, _ = self.form_for(f'?trimester_id={self.trimester.pk}')

        offered = list(form.fields['subject'].queryset)
        self.assertIn(self.subject, offered)
        self.assertNotIn(self.other_subject, offered)

    def test_the_scope_arrives_preselected(self):
        """The page already states the trimester and the subject; making the
        teacher pick them again is how the two drift apart."""
        form, _ = self.form_for(
            f'?trimester_id={self.trimester.pk}'
            f'&subject_courses_id={self.sc_t1.pk}')

        self.assertEqual(form.fields['trimester'].initial, self.trimester.pk)
        self.assertEqual(form.fields['subject'].initial, self.subject.pk)

    def test_the_subject_is_preselected_but_not_locked(self):
        """An absence for another subject in the same trimester stays one
        submit away."""
        second = Subjects.objects.create(Name='Lengua')
        Subjects_Courses.objects.create(
            subject=second, teacher=self.teacher_a,
            course=self.course, trimester=self.trimester)

        form, _ = self.form_for(f'?subject_courses_id={self.sc_t1.pk}')

        self.assertIn(second, list(form.fields['subject'].queryset))

    def test_the_school_year_is_still_fixed_by_the_course(self):
        form, _ = self.form_for()

        self.assertEqual(list(form.fields['school_year'].queryset), [self.year])
        self.assertEqual(form.fields['school_year'].initial, self.year)

    def test_a_student_outside_the_scoped_roster_is_rejected(self):
        """The narrowing is validation, not only display: the POST carries the
        same scope in its query string."""
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        response = self.as_(self.professor).post(
            f'/class/{self.course.pk}/dashboard/'
            f'?subject_courses_id={self.sc_t1.pk}',
            {
                'students': [self.student_b.pk],
                'subject': self.subject.pk,
                'trimester': self.trimester.pk,
                'school_year': self.year.pk,
                'Tipo': 'Ausencia',
            })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Ausencias.objects.filter(student=self.student_b).exists())

    def test_a_student_inside_the_scoped_roster_is_accepted(self):
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        response = self.as_(self.professor).post(
            f'/class/{self.course.pk}/dashboard/'
            f'?subject_courses_id={self.sc_t1.pk}',
            {
                'students': [self.student.pk],
                'subject': self.subject.pk,
                'trimester': self.trimester.pk,
                'school_year': self.year.pk,
                'Tipo': 'Ausencia',
            })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Ausencias.objects.filter(student=self.student).exists())

    def test_the_panel_says_which_roster_it_is_offering(self):
        """Two students on offer where the course has three is correct, but
        only legible if the panel admits which list it is using."""
        self.sc_t1.assigned_course_sections.set([self.enrolment])

        _, response = self.form_for(f'?subject_courses_id={self.sc_t1.pk}')

        self.assertContains(response, 'Solo la lista propia de Matematicas')


class ClassDashboardCostTests(AccessControlTestCase):
    """The page as a whole, not just class_metrics.

    Step 2 asserted the aggregates were flat in the roster size; steps 3, 4 and
    6 then added a template, a fragment and a scope-built form on top, each of
    which evaluates querysets of its own."""

    def enrol(self, n):
        sc = Subjects_Courses.objects.get(
            course=self.course, trimester=self.trimester)
        for i in range(n):
            extra = Students.objects.create(
                Name=f'Alumno {i:03d}', Email=f'a{i}@example.com')
            Students_Courses.objects.create(
                student=extra, course_section=self.course)
            Grade.objects.create(
                student=extra, subject=self.subject, trimester=self.trimester,
                school_year=self.year, grade=Decimal('7.00'),
                grade_type='examen', grade_type_number=i)
        return sc

    def test_the_page_does_not_query_per_student(self):
        """Rendering the register and the student select must not walk the
        roster row by row."""
        client = self.as_(self.professor)
        url = f'/class/{self.course.pk}/dashboard/'
        self.enrol(3)

        with CaptureQueriesContext(connection) as small:
            self.assertEqual(client.get(url).status_code, 200)

        self.enrol(27)

        with CaptureQueriesContext(connection) as large:
            self.assertEqual(client.get(url).status_code, 200)

        self.assertEqual(len(large), len(small))

    def test_the_htmx_fragment_costs_no_more_than_the_page(self):
        client = self.as_(self.professor)
        url = f'/class/{self.course.pk}/dashboard/'
        self.enrol(3)

        with CaptureQueriesContext(connection) as full:
            client.get(url)

        with CaptureQueriesContext(connection) as fragment:
            client.get(url, HTTP_HX_REQUEST='true')

        self.assertLessEqual(len(fragment), len(full))


class LocaleTests(AccessControlTestCase):
    """LANGUAGE_CODE is es-es. The app is Spanish, so a grade must read 2,66 —
    but the comma must not reach a CSV, and it must not reach a form control
    the browser parses."""

    def test_a_grade_renders_with_a_decimal_comma_on_the_page(self):
        Grade.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            grade=Decimal('2.66'), grade_type='examen', grade_type_number=0)

        response = self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/')

        self.assertContains(response, '2,66')
        self.assertNotContains(response, '2.66')

    def test_the_csv_export_still_uses_a_decimal_point(self):
        """The exports write Decimals through csv.writer, which calls str().
        A localized number here would move the column separator into the
        value and break every consumer."""
        Grade.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            grade=Decimal('2.66'), grade_type='examen', grade_type_number=0)

        body = self.as_(self.professor).get('/grades/csv/').content.decode()

        self.assertIn('2.66', body)
        self.assertNotIn('2,66', body)

    def test_the_datetime_control_receives_an_iso_value(self):
        """<input type="datetime-local"> only accepts ISO. Without an explicit
        widget format Django renders DATETIME_INPUT_FORMATS[0] of the active
        locale — '%d/%m/%Y %H:%M:%S' under es — and the browser silently
        blanks the control."""
        response = self.as_(self.professor).get(
            f'/class/{self.course.pk}/dashboard/')

        field = response.context['ausencia_form']['date_time']
        value = field.value()
        rendered = str(field)

        self.assertRegex(
            rendered, r'value="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"')
        self.assertNotIn('/', rendered.split('value="')[1].split('"')[0])
        self.assertIsNotNone(value)

    def test_editing_an_absence_keeps_its_date_in_the_control(self):
        """The edit form had the same defect, and there it lost a stored
        value rather than a default."""
        from .forms import AusenciaEditForm

        ausencia = Ausencias.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year, Tipo='Ausencia',
            date_time=timezone.now())

        rendered = str(AusenciaEditForm(instance=ausencia)['date_time'])

        self.assertRegex(
            rendered, r'value="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"')

    def test_a_browser_posted_iso_datetime_is_still_accepted(self):
        """Parsing was never the problem, but the locale switch is exactly the
        kind of change that would break it silently."""
        response = self.as_(self.professor).post(
            f'/class/{self.course.pk}/dashboard/',
            {
                'students': [self.student.pk],
                'subject': self.subject.pk,
                'trimester': self.trimester.pk,
                'school_year': self.year.pk,
                'Tipo': 'Retraso',
                'date_time': '2026-03-04T09:15',
            })

        self.assertEqual(response.status_code, 302)
        saved = Ausencias.objects.get(student=self.student, Tipo='Retraso')
        self.assertEqual(timezone.localtime(saved.date_time).hour, 9)
        self.assertEqual(timezone.localtime(saved.date_time).minute, 15)


class V2CascadeAssertions:
    """The three ways a v2 page breaks without looking broken.

    A mixin rather than a base class: every migrated page needs these, and
    inheriting them from a concrete TestCase would re-run that page's own
    tests once per migration.
    """

    LEGACY_CSS = ('css/navbar.css', 'css/sidebar.css',
                  'css/site-pages.css', 'css/global-styles.css')

    def assert_v2_only(self, response, status_code=200):
        self.assertTemplateUsed(response, 'base_v2.html')
        self.assertTemplateUsed(response, 'base_shell_v2.html')
        self.assertContains(response, 'css/tailwind.css',
                            status_code=status_code)
        for legacy in self.LEGACY_CSS:
            self.assertNotContains(response, legacy, status_code=status_code)

    def assert_scripts_are_self_hosted(self, response, status_code=200):
        """CSP is script-src 'self' with no unsafe-inline and no CDN."""
        self.assertContains(response, 'js/vendor/htmx', status_code=status_code)
        self.assertNotContains(response, '//unpkg.com', status_code=status_code)
        self.assertNotContains(response, '//cdn.', status_code=status_code)
        # hx-on: evals its attribute value, which the policy forbids.
        self.assertNotContains(response, 'hx-on', status_code=status_code)
        self.assertNotContains(response, 'onclick=', status_code=status_code)
        self.assertNotContains(response, 'onchange=', status_code=status_code)

    # Every hook behaviors.js binds. Read off that file rather than recalled:
    # `data-href` was missing from this list until the write-form slice, and it
    # is the one the legacy pages use most — every "button" that is really a
    # link is a `<button data-href>`.
    INERT_HOOKS = ('data-action', 'data-href', 'data-autosubmit')

    def assert_no_inert_js_hooks(self, response, status_code=200):
        """base_v2 loads htmx and nothing else — `behaviors.js` is not there.

        So any of these attributes on a v2 page is dead markup: the click or
        change does nothing, and nothing says so. No console error, no failed
        request, no visual difference.
        """
        self.assertNotContains(response, 'behaviors.js', status_code=status_code)
        for hook in self.INERT_HOOKS:
            self.assertNotContains(response, hook, status_code=status_code)

    def assert_no_leaked_template_comments(self, response, status_code=200):
        """`{# #}` is single-line only — spread it over two and Django renders
        it as text. It looks like a comment in the editor either way."""
        self.assertNotContains(response, '{#', status_code=status_code)
        self.assertNotContains(response, '{%', status_code=status_code)


class LoginPageTemplateTests(V2CascadeAssertions, AccessControlTestCase):
    """The one page that never extended base.html in the first place.

    It carried its own <head> and a fifth hand-written stylesheet, so it was
    outside the two-cascade split entirely. It extends base_v2 directly rather
    than base_shell_v2: the shell's nav is the teacher's, and a signed-out
    visitor has no business being offered it.

    Deliberately placed here rather than at the end of the file — parallel
    migration branches all append their own class at EOF, and this one does
    not need to be among them.
    """

    URL = '/'

    def test_the_login_page_is_built_on_the_v2_cascade_only(self):
        response = self.client.get(self.URL)

        self.assertTemplateUsed(response, 'mainapp/login.html')
        self.assertTemplateUsed(response, 'base_v2.html')
        self.assertContains(response, 'css/tailwind.css')
        for legacy in self.LEGACY_CSS:
            self.assertNotContains(response, legacy)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_it_does_not_pull_the_teachers_shell(self):
        """base_shell_v2's nav is "Mis clases / Eso / …" plus a student
        search. Every one of those is role-guarded, so an anonymous visitor
        would be looking at a menu of 403s."""
        response = self.client.get(self.URL)

        self.assertTemplateNotUsed(response, 'base_shell_v2.html')
        self.assertNotContains(response, 'Mis clases')

    def test_the_bespoke_login_assets_are_gone(self):
        """login.css was a fifth hand-written stylesheet; login.js shook the
        card on an empty field, which constraint validation already prevents
        because both inputs are `required`."""
        response = self.client.get(self.URL)

        self.assertNotContains(response, 'css/login.css')
        self.assertNotContains(response, 'js/login.js')

    def test_both_fields_keep_their_ids_and_the_form_still_posts(self):
        """The view reads request.POST['username'] / ['password'], and the
        labels are wired to those ids."""
        response = self.client.get(self.URL)

        self.assertContains(response, 'id="username"')
        self.assertContains(response, 'id="password"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_a_wrong_password_says_so_in_spanish_and_announces_it(self):
        response = self.client.post(
            self.URL, {'username': 'nadie', 'password': 'incorrecta'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Usuario o contraseña incorrectos')
        self.assertContains(response, 'role="alert"')

    def test_the_failure_message_does_not_distinguish_the_two_cases(self):
        """Telling "no such user" apart from "wrong password" is a user
        enumeration oracle. The view already refuses to; so must the page."""
        unknown = self.client.post(
            self.URL, {'username': 'nadie', 'password': 'incorrecta'})
        known = self.client.post(
            self.URL, {'username': self.professor.username,
                       'password': 'not-the-password'})

        self.assertContains(unknown, 'Usuario o contraseña incorrectos')
        self.assertContains(known, 'Usuario o contraseña incorrectos')

    def test_a_missing_field_gets_its_own_spanish_message(self):
        response = self.client.post(self.URL, {'username': '', 'password': ''})

        self.assertContains(response, 'Introduce el usuario y la contraseña')

    def test_a_correct_password_still_routes_by_role(self):
        """Guard against over-tightening: the happy path must survive."""
        response = self.client.post(
            self.URL, {'username': self.professor.username, 'password': PW})

        self.assertRedirects(response, '/teacher/')

    def test_the_lockout_state_explains_itself(self):
        """settings.AXES_LOCKOUT_TEMPLATE is this same file, and axes renders
        it with `failure_limit` and no `error`. Before this block a locked-out
        account got a blank form back and no reason — which reads exactly like
        a mistyped password, so the obvious response was to keep retrying."""
        from django.template.loader import render_to_string

        html = render_to_string('mainapp/login.html',
                                {'failure_limit': 10, 'username': 'prof1'})

        self.assertIn('Cuenta bloqueada temporalmente tras 10', html)

    def test_the_ordinary_page_makes_no_lockout_claim(self):
        response = self.client.get(self.URL)

        self.assertNotContains(response, 'Cuenta bloqueada')


class ReassignStudentsTests(AccessControlTestCase):
    """The reassign view moved an arbitrary enrolment, not the right one.

    `Students_Courses` is unique on `(student, course_section)`, so a student
    holds one row per course and therefore one per year they have been
    enrolled. The view looked the enrolment up with
    `.filter(student=student).first()` — no ordering, no year — and repointed
    whatever came back. For a student who has progressed a year, that could be
    last year's class, and the current year's enrolment would be left alone.

    It went unnoticed because a student who has only ever been in one course
    has exactly one row, which is every student in a freshly seeded database.
    """

    URL = '/reassign-students/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.last_year = School_year.objects.create(year='2024-2025')
        cls.old_class = Course.objects.create(
            Tipo='Eso', Section='3A', school_year=cls.last_year)
        cls.this_class = Course.objects.create(
            Tipo='Eso', Section='4A', school_year=cls.year)
        cls.target_class = Course.objects.create(
            Tipo='Eso', Section='4B', school_year=cls.year)
        # A student of its own: the shared fixture already enrols `student` in
        # `course` for the current year, which is a second same-year row and
        # would obscure which enrolment the view actually picked.
        cls.mover = Students.objects.create(
            Name='Mover Test', Email='mover@example.com')

    def enrol(self, *courses):
        for course in courses:
            Students_Courses.objects.create(
                student=self.mover, course_section=course)

    def reassign(self, course):
        return self.as_(self.admin).post(
            self.URL, {'assignments': [f'{self.mover.pk}:{course.pk}']})

    def rows(self):
        return {sc.course_section_id for sc
                in Students_Courses.objects.filter(student=self.mover)}

    def test_it_moves_the_enrolment_for_the_destinations_own_year(self):
        self.enrol(self.old_class, self.this_class)

        self.reassign(self.target_class)

        self.assertIn(self.old_class.pk, self.rows())
        self.assertIn(self.target_class.pk, self.rows())
        self.assertNotIn(self.this_class.pk, self.rows())

    def test_last_years_enrolment_is_left_alone(self):
        """The regression that mattered: history is not a place to write to."""
        self.enrol(self.old_class, self.this_class)

        self.reassign(self.target_class)

        self.assertTrue(Students_Courses.objects.filter(
            student=self.mover, course_section=self.old_class).exists())

    def test_a_student_with_no_enrolment_that_year_gains_one(self):
        """Only last year on file, so this is an addition rather than a move
        and the old row still must not be touched."""
        self.enrol(self.old_class)

        self.reassign(self.target_class)

        self.assertEqual(self.rows(), {self.old_class.pk, self.target_class.pk})

    def test_reassigning_into_the_course_they_are_already_in_is_a_no_op(self):
        """Repointing a row onto itself would trip unique_together and be
        reported as a failure for a request that is already satisfied."""
        self.enrol(self.target_class)

        self.reassign(self.target_class)

        self.assertEqual(self.rows(), {self.target_class.pk})
        self.assertEqual(Students_Courses.objects.filter(
            student=self.mover).count(), 1)

    def test_the_single_enrolment_case_still_works(self):
        """Guard against over-tightening: the common path must survive."""
        self.enrol(self.this_class)

        self.reassign(self.target_class)

        self.assertEqual(self.rows(), {self.target_class.pk})

    def test_a_malformed_pair_is_counted_and_does_not_500(self):
        response = self.as_(self.admin).post(
            self.URL,
            {'assignments': ['not-a-pair', f'999999:{self.target_class.pk}']})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows(), set())

    def test_the_outcome_messages_are_spanish(self):
        self.enrol(self.this_class)

        response = self.as_(self.admin).post(
            self.URL,
            {'assignments': [f'{self.mover.pk}:{self.target_class.pk}']},
            follow=True)

        self.assertContains(response, 'reasignad')
        self.assertNotContains(response, 'reassigned successfully')

    def test_reassignment_is_still_administrator_only(self):
        for user in (self.professor, self.pupil, self.tutor):
            with self.subTest(user=user.username):
                self.assertEqual(
                    self.as_(user).get(self.URL).status_code, 403)


class AdminFlowTemplateTests(V2CascadeAssertions, AccessControlTestCase):
    """Stage 3: the three script-free administrator forms.

    These were standalone `<!DOCTYPE>` documents that extended nothing, which
    is why no `{% extends %}` sweep ever counted them. They are the cheapest of
    the eight — no jQuery, no AJAX, no dependent selects — so they are what
    establishes that an administrator page can sit on the shared shell at all.

    Placed here rather than at the end of the file on purpose: parallel slices
    append their classes at EOF, and this one does not need to be among them.
    """

    def year_url(self):
        return '/adminage/create-school-year/'

    def courses_url(self):
        return '/adminage/create-courses/'

    # --- create_school_year -------------------------------------------------

    def test_the_school_year_form_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.admin).get(self.year_url())

        self.assertTemplateUsed(response, 'adminage/create_school_year.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_admin_pages_no_longer_carry_their_own_document(self):
        """Each of these was a full <!DOCTYPE> with its own inline <style>."""
        response = self.as_(self.admin).get(self.year_url())
        body = response.content.decode()

        self.assertEqual(body.lower().count('<!doctype'), 1)
        self.assertNotIn('<style', body)
        self.assertNotIn('global-styles.css', body)

    def test_the_admin_nav_marks_the_page_you_are_on(self):
        """The shell's administrator branch was written before any admin page
        could reach it. This is the first page that actually does."""
        response = self.as_(self.admin).get(self.year_url())

        self.assertContains(response, 'Administración')
        self.assertContains(response, 'aria-current="page"')

    def test_the_school_year_field_is_spanish_and_carries_the_control_class(self):
        """Django renders the widget, so `ctl` is set in forms.py — a class
        written into the template would never reach the input."""
        response = self.as_(self.admin).get(self.year_url())

        self.assertContains(response, 'Año escolar')
        self.assertContains(response, 'class="ctl"')
        self.assertNotContains(response, 'Define School Year')

    def test_creating_a_school_year_still_works_through_the_rebuilt_page(self):
        response = self.as_(self.admin).post(
            self.year_url(), {'year': '2031-2032'})

        self.assertTrue(School_year.objects.filter(year='2031-2032').exists())
        self.assertEqual(response.status_code, 302)

    def test_an_invalid_year_re_renders_with_its_error_visible(self):
        response = self.as_(self.admin).post(self.year_url(), {'year': ''})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'text-bad')

    def test_the_admin_forms_are_still_administrator_only(self):
        """The rebuild must not widen what the security work narrowed."""
        for user in (self.professor, self.pupil, self.tutor):
            with self.subTest(user=user.username):
                self.assertEqual(
                    self.as_(user).get(self.year_url()).status_code, 403)

    # --- create_courses, both steps -----------------------------------------

    def test_step_one_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.admin).get(
            f'{self.courses_url()}?school_year_id={self.year.pk}')

        self.assertTemplateUsed(response, 'adminage/create_courses_step1.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)
        self.assertContains(response, 'Tipo de curso')
        self.assertNotContains(response, 'Course Type')

    def test_step_one_keeps_the_hidden_step_the_view_branches_on(self):
        """`step=select_type` is not decoration — the view reads it to decide
        which half of create_courses_sections_view runs."""
        response = self.as_(self.admin).get(
            f'{self.courses_url()}?school_year_id={self.year.pk}')

        self.assertContains(response, 'name="step" value="select_type"')
        self.assertContains(response, f'school_year_id={self.year.pk}')

    def test_step_two_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.admin).post(
            f'{self.courses_url()}?school_year_id={self.year.pk}',
            {'step': 'select_type', 'course_tipo': 'Eso',
             'school_year': self.year.pk})

        self.assertTemplateUsed(response, 'adminage/create_courses_step2.html')
        self.assert_v2_only(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_step_two_offers_one_row_per_level_and_keeps_its_hidden_names(self):
        """MAIN_COURSES fixes the levels per type — Eso is 1-4. Dropping
        `main_course_name` would lose which level each row belongs to."""
        response = self.as_(self.admin).post(
            f'{self.courses_url()}?school_year_id={self.year.pk}',
            {'step': 'select_type', 'course_tipo': 'Eso',
             'school_year': self.year.pk})
        body = response.content.decode()

        self.assertEqual(body.count('name="form-'), body.count('name="form-'))
        self.assertContains(response, 'main_course_name')
        self.assertContains(response, 'name="course_tipo" value="Eso"')
        self.assertContains(response, 'name="step" value="confirm_sections"')
        self.assertContains(response, 'Nº de secciones')
        self.assertNotContains(response, 'No. of Sections')

    def test_the_help_text_is_actually_addressable(self):
        """Django emits aria-describedby="id_..._helptext" on any widget whose
        field has help text. The first draft rendered the text in a span with
        no id, so every control pointed at nothing — invisible to a sighted
        reader, and caught by reading the markup rather than by a test."""
        response = self.as_(self.admin).post(
            f'{self.courses_url()}?school_year_id={self.year.pk}',
            {'step': 'select_type', 'course_tipo': 'Eso',
             'school_year': self.year.pk})
        body = response.content.decode()

        for described in re.findall(r'aria-describedby="([^"]+)"', body):
            with self.subTest(target=described):
                self.assertIn(f'id="{described}"', body)

    def test_step_two_help_text_is_spanish(self):
        response = self.as_(self.admin).post(
            f'{self.courses_url()}?school_year_id={self.year.pk}',
            {'step': 'select_type', 'course_tipo': 'Eso',
             'school_year': self.year.pk})

        self.assertContains(response, 'creará 1A, 1B y 1C')
        self.assertNotContains(response, 'will create')

    def test_the_flow_still_creates_the_courses_it_promises(self):
        """The whole point of the two steps. Eso 1-4, two sections each."""
        payload = {
            'step': 'confirm_sections',
            'course_tipo': 'Eso',
            'school_year': self.year.pk,
            'form-TOTAL_FORMS': '4',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
        }
        for i, level in enumerate((1, 2, 3, 4)):
            payload[f'form-{i}-main_course_name'] = str(level)
            payload[f'form-{i}-num_subsections'] = '2'

        self.as_(self.admin).post(
            f'{self.courses_url()}?school_year_id={self.year.pk}', payload)

        self.assertTrue(Course.objects.filter(
            Tipo='Eso', Section='1A', school_year=self.year).exists())
        self.assertTrue(Course.objects.filter(
            Tipo='Eso', Section='4B', school_year=self.year).exists())


class MigratedPageTemplateTests(V2CascadeAssertions, AccessControlTestCase):
    """Stage 2: three more pages off base.html.

    The cascade assertion is the load-bearing one. Tailwind's Preflight and the
    four hand-written stylesheets collide, so a page that pulls both renders
    wrong in ways a screenshot of one viewport can easily miss.
    """

    # --- section_courses ----------------------------------------------------

    def test_section_listing_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.professor).get('/section/eso/courses/')

        self.assertTemplateUsed(response, 'mainapp/section_courses.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_year_filter_is_links_rather_than_a_scripted_select(self):
        """The legacy control was a <select data-autosubmit>. Real hrefs need
        no JavaScript at all, and produce the same URL the view already reads."""
        response = self.as_(self.professor).get('/section/eso/courses/')

        self.assertContains(response, f'?school_year_id={self.year.pk}')
        self.assertNotContains(response, 'data-autosubmit')

    def test_the_section_listing_shows_the_courses_it_counts(self):
        response = self.as_(self.professor).get('/section/eso/courses/')

        self.assertContains(response, str(self.course))
        self.assertContains(response, '1 curso')

    def test_an_empty_section_says_so_instead_of_rendering_a_bare_table(self):
        response = self.as_(self.professor).get('/section/ib/courses/')

        self.assertContains(response, 'No hay cursos en esta sección')

    # --- search_results -----------------------------------------------------

    def test_search_results_are_built_on_the_v2_cascade_only(self):
        response = self.as_(self.professor).get('/search/?q=ana')

        self.assertTemplateUsed(response, 'mainapp/search_results.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_a_result_carries_the_same_initials_as_the_register(self):
        """One glyph rule across both lists: direction C has no icon tiles, so
        the initials are the only per-student mark and must not diverge."""
        response = self.as_(self.professor).get('/search/?q=ana')

        self.assertContains(response, self.student.Name)
        self.assertContains(response, '>AL<')

    def test_a_course_scoped_search_keeps_its_scope_in_the_form(self):
        """Losing the hidden field would silently widen the next query."""
        response = self.as_(self.professor).get(
            f'/search/?q=ana&course={self.course.pk}')

        self.assertContains(
            response, f'name="course" value="{self.course.pk}"')

    def test_an_empty_query_prompts_rather_than_reporting_no_matches(self):
        """`search_students` returns nothing at all without a query, which is
        not the same statement as "this student does not exist"."""
        response = self.as_(self.professor).get('/search/')

        self.assertContains(response, 'Escribe un nombre')
        self.assertNotContains(response, 'No se encontraron estudiantes')

    def test_a_query_with_no_matches_says_so(self):
        response = self.as_(self.professor).get('/search/?q=zzzzz')

        self.assertContains(response, 'No se encontraron estudiantes')

    # --- forbidden ----------------------------------------------------------

    def test_the_403_page_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.pupil).get('/teacher/')

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'forbidden.html')
        self.assert_v2_only(response, status_code=403)
        self.assert_scripts_are_self_hosted(response, status_code=403)
        self.assert_no_inert_js_hooks(response, status_code=403)
        self.assert_no_leaked_template_comments(response, status_code=403)

    def test_the_403_page_still_names_the_account_that_was_refused(self):
        """A wrong account and a wrong URL are different problems, and only
        this block tells them apart."""
        response = self.as_(self.pupil).get('/teacher/')

        self.assertContains(response, self.pupil.username, status_code=403)
        self.assertContains(response, 'Acceso prohibido', status_code=403)

    def test_the_unlinked_teacher_keeps_its_own_explanation(self):
        """It names the one fix an administrator can actually apply."""
        response = self.as_(self.unlinked_professor).get('/teacher/')

        self.assertContains(
            response, 'no está vinculada a ningún registro', status_code=403)

    def test_a_plain_role_denial_does_not_claim_a_missing_teacher_link(self):
        response = self.as_(self.pupil).get('/teacher/')

        self.assertNotContains(
            response, 'no está vinculada a ningún registro', status_code=403)


class TeacherDashboardTemplateTests(AccessControlTestCase):
    """The professor's landing page, second off base.html.

    The year control is the interesting part: the legacy page used a
    `<select data-autosubmit>`, and base_v2 loads htmx and nothing else — so
    that control would have been inert on the rebuilt page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.bach = Course.objects.create(
            Tipo='Bachillerato', Section='1A', school_year=cls.year)
        Subjects_Courses.objects.create(
            subject=cls.subject, teacher=cls.teacher_a, course=cls.bach,
            trimester=cls.trimester)
        cls.old_year = School_year.objects.create(year='2024-2025')

    def get(self, query=''):
        return self.as_(self.professor).get(f'/teacher/{query}')

    def test_page_is_built_on_the_v2_cascade_only(self):
        response = self.get()

        self.assertTemplateUsed(response, 'base_v2.html')
        self.assertContains(response, 'css/tailwind.css')
        for legacy in ('css/navbar.css', 'css/sidebar.css',
                       'css/site-pages.css', 'css/global-styles.css'):
            self.assertNotContains(response, legacy)

    def test_the_year_control_is_links_not_an_inert_select(self):
        """behaviors.js is not loaded on a v2 page, so data-autosubmit would
        do nothing and the teacher could not change the year at all."""
        response = self.get()

        self.assertNotContains(response, 'data-autosubmit')
        self.assertContains(response, f'href="?school_year={self.year.pk}"')
        self.assertContains(response, f'href="?school_year={self.old_year.pk}"')

    def test_the_counts_are_per_type(self):
        response = self.get()

        self.assertEqual(len(response.context['eso_courses']), 1)
        self.assertEqual(len(response.context['bachillerato_courses']), 1)
        self.assertEqual(len(response.context['ib_courses']), 0)
        self.assertEqual(len(response.context['courses']), 2)

    def test_an_empty_group_says_which_group_is_empty(self):
        """Three identical "no hay cursos" lines tell a teacher nothing."""
        response = self.get()

        self.assertContains(response, 'ninguna clase de IB')
        self.assertNotContains(response, 'ninguna clase de Eso')

    def test_a_year_with_no_classes_is_not_an_error(self):
        response = self.get(f'?school_year={self.old_year.pk}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['courses']), 0)
        self.assertEqual(response.context['selected_school_year'], self.old_year)

    def test_an_unknown_year_falls_back_instead_of_raising(self):
        """Stale bookmarks are routine; the newest year is a better page than
        a 500. Same rule as resolve_class_scope."""
        response = self.get('?school_year=999999')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_school_year'], self.year)

    def test_the_nav_marks_the_page_you_are_on(self):
        response = self.get()

        self.assertContains(response, 'aria-current="page"')

    def test_it_still_lists_only_this_teachers_classes(self):
        """The rebuild must not widen the scope the security work narrowed."""
        response = self.get()

        sections = [c.CourseID for c in response.context['courses']]
        self.assertIn(self.course.CourseID, sections)
        self.assertNotIn(self.other_course.CourseID, sections)


class WriteFormTemplateTests(V2CascadeAssertions, AccessControlTestCase):
    """`grade_form` and `ausencia_form`, stage 2's write pair.

    They inherit the cascade / CSP / inert-hook assertions rather than
    restating them: the same three ways a v2 page silently breaks apply to a
    form, and the trimester cascade is exactly the kind of control the
    `behaviors.js` note warns about.
    """

    def grade_url(self):
        return f'/student/{self.student.pk}/grade/new/'

    def ausencia_url(self):
        return f'/student/{self.student.pk}/ausencia/new/'

    # --- grade_form ---------------------------------------------------------

    def test_grade_form_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.professor).get(self.grade_url())

        self.assertTemplateUsed(response, 'mainapp/grade_form.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_grade_form_carries_no_jquery(self):
        """The cascade was jQuery plus a nonce'd inline block. base_v2 loads
        neither, so leaving the markup would have been dead weight at best."""
        response = self.as_(self.professor).get(self.grade_url())

        self.assertNotContains(response, 'jquery')

    def test_the_trimester_cascade_is_htmx_against_the_same_endpoint(self):
        response = self.as_(self.professor).get(self.grade_url())

        self.assertContains(response, 'hx-get="/ajax/load-trimesters/"')
        self.assertContains(response, 'hx-target="#id_trimester"')

    def test_the_trimester_list_is_usable_before_any_javascript_runs(self):
        """create_edit_grade pre-selects the newest year, so the first paint
        can already offer that year's trimesters. Without this the page is
        unusable with JavaScript off."""
        response = self.as_(self.professor).get(self.grade_url())

        self.assertContains(response, f'Trimestre {self.trimester.Name}')

    def test_cancel_is_a_link_back_to_the_student_not_an_inert_button(self):
        """It was `<button data-action="back">`, which needs behaviors.js."""
        response = self.as_(self.professor).get(self.grade_url())

        self.assertContains(
            response, f'href="/students/{self.student.pk}/dashboard/"')

    def test_the_form_labels_are_spanish(self):
        response = self.as_(self.professor).get(self.grade_url())

        self.assertContains(response, 'Año escolar')
        self.assertContains(response, 'Tipo de nota')

    def test_the_student_stays_a_hidden_input(self):
        """Rendering it as a select would hand the browser a choice the view
        deliberately ignores."""
        response = self.as_(self.professor).get(self.grade_url())

        self.assertContains(response, 'type="hidden" name="student"')

    def test_an_invalid_grade_re_renders_with_its_error_visible(self):
        response = self.as_(self.professor).post(self.grade_url(), {
            'student': self.student.pk,
            'school_year': self.year.pk,
            'trimester': self.trimester.pk,
            'subject': self.subject.pk,
            'grade_type': 'examen',
            'grade_type_number': 1,
            'grade': '99',
            'comments': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'text-bad')
        self.assertFalse(Grade.objects.filter(grade_type_number=1).exists())

    def test_the_happy_path_still_saves_through_the_rebuilt_page(self):
        response = self.as_(self.professor).post(self.grade_url(), {
            'student': self.student.pk,
            'school_year': self.year.pk,
            'trimester': self.trimester.pk,
            'subject': self.subject.pk,
            'grade_type': 'examen',
            'grade_type_number': 4,
            'grade': '8.25',
            'comments': '',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Grade.objects.filter(
            student=self.student, grade_type_number=4).exists())

    # --- the trimester endpoint ---------------------------------------------

    def test_the_endpoint_returns_options_rather_than_json(self):
        response = self.as_(self.professor).get(
            f'/ajax/load-trimesters/?school_year={self.year.pk}')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'<option value="{self.trimester.pk}">')
        self.assertContains(response, f'Trimestre {self.trimester.Name}')

    def test_the_endpoint_names_the_option_the_same_way_the_form_does(self):
        """Two renderings of one list. If they diverge, changing the year
        renames every trimester on screen."""
        page = self.as_(self.professor).get(self.grade_url())
        fragment = self.as_(self.professor).get(
            f'/ajax/load-trimesters/?school_year={self.year.pk}')

        label = f'Trimestre {self.trimester.Name}'
        self.assertContains(page, label)
        self.assertContains(fragment, label)

    def test_a_blank_year_yields_an_empty_list_rather_than_a_500(self):
        response = self.as_(self.professor).get('/ajax/load-trimesters/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Trimestre')

    def test_the_endpoint_is_still_professor_only(self):
        response = self.as_(self.pupil).get(
            f'/ajax/load-trimesters/?school_year={self.year.pk}')

        self.assertEqual(response.status_code, 403)

    # --- ausencia_form ------------------------------------------------------

    def test_ausencia_form_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.professor).get(self.ausencia_url())

        self.assertTemplateUsed(response, 'mainapp/ausencia_form.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_absence_labels_match_the_panel_on_the_class_dashboard(self):
        """Same model, same words — the single-student form is the panel's
        sibling, not a second dialect."""
        response = self.as_(self.professor).get(self.ausencia_url())

        for label in ('Materia', 'Trimestre', 'Año escolar', 'Fecha y hora'):
            self.assertContains(response, label)

    def test_the_datetime_control_keeps_its_iso_format_on_edit(self):
        """es-ES renders a value <input type="datetime-local"> silently blanks;
        LocaleTests pins the widget, this pins the rendered page."""
        ausencia = Ausencias.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            Tipo='Falta', date_time=timezone.now())

        response = self.as_(self.professor).get(
            f'/student/edit/ausencia/{ausencia.pk}/')

        self.assertContains(response, 'type="datetime-local"')
        self.assertContains(
            response,
            f'value="{timezone.localtime(ausencia.date_time).strftime("%Y-%m-%dT%H:%M")}"')

    def test_the_absence_controls_carry_the_v2_control_class(self):
        response = self.as_(self.professor).get(self.ausencia_url())

        self.assertContains(response, 'class="ctl"')


class CsvPageTemplateTests(V2CascadeAssertions, AccessControlTestCase):
    """Stage 2: the two CSV pages off base.html.

    `CsvImportTests` and `CsvRoundTripTests` already pin what the importer
    *does*; this class pins what the teacher is shown. The error path gets the
    attention, because it is this page's real job and the one part of it a
    green import never exercises.
    """

    HEADER = ('Nombre_Estudiante,Asignatura,Trimestre,Año_Escolar,'
              'Nota,Tipo_Nota,Numero_Tipo_Nota,Comentarios')

    def import_url(self, scoped=True):
        if scoped:
            return f'/import/grades/{self.course.CourseID}/'
        return '/import/grades/'

    def download_url(self):
        return f'/class/{self.course.CourseID}/grades/download/'

    def a_grade(self):
        return Grade.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year,
            grade=Decimal('7.5'), grade_type='examen', grade_type_number=1)

    def good_row(self):
        return f'{self.student.Name},{self.subject.Name},1,2025-2026,7.5,examen,1,'

    def row_with_unknown_year(self):
        return f'{self.student.Name},{self.subject.Name},1,9999-0000,6,examen,2,'

    def blank_grade_row(self):
        return f'{self.student.Name},{self.subject.Name},1,2025-2026,,examen,3,'

    def upload(self, *rows):
        """Post a CSV and return the rendered page.

        Deliberately not `follow=True`: the result is rendered in place rather
        than redirected to, so following would hide a regression that turned
        it back into a redirect.
        """
        body = '\n'.join([self.HEADER, *rows]).encode('utf-8')
        upload = SimpleUploadedFile('grades.csv', body, content_type='text/csv')
        return self.as_(self.professor).post(
            self.import_url(), {'csv_file': upload})

    # --- import_grades ------------------------------------------------------

    def test_the_import_page_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.professor).get(self.import_url())

        self.assertTemplateUsed(response, 'mainapp/import_grades.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_unscoped_import_page_is_on_the_v2_cascade_too(self):
        """`import_grades` serves two routes and only one carries a course, so
        the course-less one is its own render path."""
        response = self.as_(self.professor).get(self.import_url(scoped=False))

        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)
        # No course, so there is no class list to offer a template for.
        self.assertNotContains(response, 'download/class-list')

    def test_a_class_scoped_import_offers_that_class_own_template(self):
        response = self.as_(self.professor).get(self.import_url())

        self.assertContains(
            response, f'/download/class-list/{self.course.CourseID}/')

    def test_the_error_page_is_still_on_the_v2_cascade_only(self):
        """The POST response is a full page render rather than a redirect, so
        it is its own migration surface — and the one a clean upload hides."""
        response = self.upload(self.row_with_unknown_year())

        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_a_failed_row_is_named_by_its_line_number_and_its_reason(self):
        """A teacher fixes a file, not a paragraph: the file's own row number
        is a column and the reason sits beside it."""
        response = self.upload(self.blank_grade_row(),
                               self.row_with_unknown_year())

        self.assertContains(response, 'Filas no importadas')
        self.assertContains(response, 'Falta la nota.')
        self.assertContains(response, '9999-0000')
        # Row 2 is the first data line, row 3 the second — the header is 1.
        self.assertContains(response, '>2</div>')
        self.assertContains(response, '>3</div>')

    def test_an_error_never_echoes_the_student_name_from_the_file(self):
        """Re-pinned at the template: the row is rendered in a browser and a
        roster is PII. `CsvImportTests` pins the message, this pins the page."""
        response = self.upload(
            f'Nombre Inventado,{self.subject.Name},1,2025-2026,6,examen,1,')

        self.assertNotContains(response, 'Nombre Inventado')
        self.assertContains(response, 'Alumno no encontrado')

    def test_the_summary_counts_every_outcome_of_the_upload(self):
        response = self.upload(self.good_row(), self.row_with_unknown_year())

        self.assertContains(response, 'Filas leídas')
        self.assertContains(response, 'Notas creadas')
        self.assertContains(response, 'Notas actualizadas')
        self.assertContains(response, 'Filas con error')

    def test_an_error_free_import_shows_no_error_table(self):
        """An empty "Filas no importadas" heading reads as a failed import."""
        response = self.upload(self.good_row())

        self.assertNotContains(response, 'Filas no importadas')
        self.assertContains(response, 'Filas leídas')

    def test_a_first_visit_shows_no_summary_at_all(self):
        """Zeroes on a GET would claim an import ran and did nothing."""
        response = self.as_(self.professor).get(self.import_url())

        self.assertNotContains(response, 'Filas leídas')
        self.assertNotContains(response, 'Filas no importadas')

    def test_row_errors_are_not_also_repeated_as_message_banners(self):
        """They used to be: one banner per row, capped at ten. Rendering both
        the table and the banners is the wall of text the table replaces."""
        response = self.upload(self.row_with_unknown_year())

        self.assertEqual(response.content.count(b'9999-0000'), 1)

    def test_a_whole_file_refusal_is_a_message_rather_than_a_table(self):
        """"Wrong extension" is one statement about the upload — no row was
        ever parsed, so there is nothing to tabulate."""
        bad = SimpleUploadedFile('grades.txt', b'x', content_type='text/plain')
        response = self.as_(self.professor).post(
            self.import_url(), {'csv_file': bad})

        self.assertContains(response, 'El archivo debe ser un CSV.')
        self.assertNotContains(response, 'Filas no importadas')
        self.assertNotContains(response, 'Filas leídas')

    def test_the_import_page_names_the_headers_the_importer_reads(self):
        """Three header sets exist and only `download_class_list`'s is
        accepted. Copy naming the wrong one costs a whole round trip."""
        response = self.as_(self.professor).get(self.import_url())

        for header in ('Nombre_Estudiante', 'Asignatura', 'Trimestre',
                       'Año_Escolar', 'Nota', 'Tipo_Nota',
                       'Numero_Tipo_Nota', 'Comentarios'):
            with self.subTest(header=header):
                self.assertContains(response, header)
        self.assertContains(response, 'Lista de clase')

    # --- class_grades_download ---------------------------------------------

    def test_the_download_page_is_built_on_the_v2_cascade_only(self):
        self.a_grade()
        response = self.as_(self.professor).get(self.download_url())

        self.assertTemplateUsed(response, 'mainapp/class_grades_download.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_empty_download_page_is_on_the_v2_cascade_too(self):
        """The empty state is a different branch of the same template."""
        response = self.as_(self.professor).get(self.download_url())

        self.assert_v2_only(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_download_form_is_never_boosted(self):
        """Boosting a CSV response fetches it over XHR and discards it, so the
        file silently never reaches disk. The class dashboard scopes its boost
        to the scope bar for exactly this reason."""
        self.a_grade()
        response = self.as_(self.professor).get(self.download_url())

        self.assertNotContains(response, 'hx-boost')
        self.assertNotContains(response, 'hx-post')
        self.assertNotContains(response, 'hx-get')

    def test_the_filter_controls_carry_the_v2_control_class(self):
        self.a_grade()
        response = self.as_(self.professor).get(self.download_url())

        for field in ('subject', 'trimester', 'school_year', 'grade_type'):
            with self.subTest(field=field):
                self.assertContains(response, f'class="ctl" id="{field}"')

    def test_a_class_with_no_grades_says_so_instead_of_empty_filters(self):
        """Every filter list is derived from grades that already exist, so with
        none the form can only offer four "Todas" and a button that yields a
        file holding one header line."""
        response = self.as_(self.professor).get(self.download_url())

        self.assertContains(response, 'no tiene ninguna nota registrada')
        self.assertNotContains(response, 'Descargar CSV')

    def test_the_download_page_says_its_headers_are_not_reimportable(self):
        """This export and the importer do not share a header set. The page
        used to say nothing at all, which reads as though they do."""
        self.a_grade()
        response = self.as_(self.professor).get(self.download_url())

        self.assertContains(response, 'Estudiante')
        self.assertContains(response, 'no</b> son las que acepta')
        self.assertContains(response, 'Lista de clase')

    def test_the_download_itself_still_returns_a_csv(self):
        """The migration must not have turned the POST into a page render."""
        self.a_grade()
        response = self.as_(self.professor).post(self.download_url(), {})

        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn(b'Estudiante', response.content)


class StudentDashboardTemplateTests(V2CascadeAssertions, AccessControlTestCase):
    """`student_dashboard_content` migrated onto base_shell_v2.

    The largest of the four pages left on `base.html`, the destination both
    write forms redirect to, and the only migrated template reached by three
    different roles — a professor at `/students/<id>/dashboard/`, and a student
    or tutor through the include in `student_file.html`.

    Two things make it worth pinning beyond the cascade. Its role branching is
    an authorization surface rendered in a template: a student reaching an
    "Editar" link would be a real widening, not a cosmetic one. And every one
    of its filter controls was a `data-autosubmit` select or a `data-href`
    button, all of which are inert on a v2 page and silently so.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A second trimester of the same year, so "filter down to nothing" is
        # expressible without inventing a year the fixtures do not have.
        cls.other_trimester = Trimester.objects.create(
            Name=2, school_year=cls.year)
        # One failing, unnumbered, commented grade and one carrying a real
        # grade_type_number, so "0 means unnumbered" is testable against a
        # row where the number is genuinely an ordinal.
        cls.grade = Grade.objects.create(
            student=cls.student, subject=cls.subject, trimester=cls.trimester,
            school_year=cls.year, grade=Decimal('2.66'), grade_type='examen',
            grade_type_number=0, comments='Mala nota')
        cls.numbered_grade = Grade.objects.create(
            student=cls.student, subject=cls.subject, trimester=cls.trimester,
            school_year=cls.year, grade=Decimal('7.5'), grade_type='parcial',
            grade_type_number=3)
        cls.ausencia = Ausencias.objects.create(
            student=cls.student, subject=cls.subject, trimester=cls.trimester,
            school_year=cls.year, Tipo='Retraso')

    def url(self, query=''):
        return f'/students/{self.student.pk}/dashboard/{query}'

    def scoped(self):
        return (f'?school_year_id={self.year.pk}'
                f'&trimester_id={self.trimester.pk}')

    # --- the cascade -------------------------------------------------------

    def test_the_student_dashboard_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.professor).get(self.url())

        self.assertTemplateUsed(
            response, 'mainapp/student_dashboard_content.html')
        self.assertTemplateUsed(response, 'mainapp/_student_record.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_cascade_holds_once_the_filters_are_applied(self):
        """The filtered page renders different branches — scope-bar active
        states, empty tables — so a leaked comment can hide in either one."""
        response = self.as_(self.professor).get(self.url(self.scoped()))

        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_record_partial_is_shell_free_so_it_stays_includable(self):
        """`student_file.html` includes the record for the student and tutor
        roles. An `{%` `extends %}` or a block in the partial would drag a whole
        second document into that page — which is the bug the split exists to
        remove, and it renders without erroring, so only this catches it.
        """
        from django.template.loader import get_template

        source = get_template('mainapp/_student_record.html').template.source

        self.assertNotIn('{% extends', source)
        self.assertNotIn('{% block', source)
        # The same partial renders under two routes, so it must filter itself
        # rather than name one of them.
        self.assertNotIn("{% url 'student_dashboard_content'", source)
        self.assertNotIn("{% url 'student_dashboard'", source)
        self.assertIn('href="?', source)

    # --- the filters -------------------------------------------------------

    def test_the_filters_are_links_rather_than_scripted_selects(self):
        """Both were `<select data-autosubmit>`, which base_v2 cannot run.

        Real hrefs produce the same URLs the view already reads, need no
        JavaScript, and give the CSP nothing to block.
        """
        response = self.as_(self.professor).get(
            self.url(f'?school_year_id={self.year.pk}'))

        self.assertContains(
            response, f'href="?school_year_id={self.year.pk}"')
        self.assertContains(
            response,
            f'href="?school_year_id={self.year.pk}'
            f'&amp;trimester_id={self.trimester.pk}"')
        self.assertNotContains(response, 'data-autosubmit')
        self.assertNotContains(response, '<select')

    def test_the_trimester_links_wait_for_a_year_to_be_chosen(self):
        """A trimester belongs to one year, and the view offers none until a
        year is picked. The legacy control was a *disabled* select; saying why
        beats rendering a dead one."""
        response = self.as_(self.professor).get(self.url())

        self.assertNotContains(response, 'trimester_id=')
        self.assertContains(response, 'Elige un año escolar')

    def test_a_year_link_never_carries_a_trimester_of_the_year_it_leaves(self):
        """`student_dashboard_content` does not validate `trimester_id` against
        the year, so a stale one would silently empty both tables rather than
        being dropped. The year links therefore carry no trimester at all."""
        import re

        response = self.as_(self.professor).get(self.url(self.scoped()))
        body = response.content.decode()

        year_links = [h for h in re.findall(r'href="\?([^"]*)"', body)
                      if 'school_year_id' in h and 'trimester_id' not in h]
        self.assertIn(f'school_year_id={self.year.pk}', year_links)

    def test_the_tutor_filter_links_all_carry_the_selected_child(self):
        """A tutor's child lives in `?child=` alone and is re-read per request.
        Drop it from one link and the tutor silently jumps back to their first
        child on the next filter click."""
        import re

        response = self.as_(self.tutor).get('/student/?child=0')
        body = response.content.decode()

        self.assertContains(response, 'href="?child=0"')
        self.assertContains(
            response, f'href="?child=0&amp;school_year_id={self.year.pk}"')
        for href in re.findall(r'href="\?([^"]*)"', body):
            self.assertTrue(
                href.startswith('child=0'),
                f'filter link {href!r} loses the tutor\'s selected child')

    def test_the_csv_link_carries_the_filter_on_screen(self):
        """Otherwise the exported file and the visible table disagree."""
        response = self.as_(self.professor).get(self.url(self.scoped()))

        self.assertContains(
            response,
            f'/grades/csv/{self.student.pk}/?school_year_id={self.year.pk}'
            f'&amp;trimester_id={self.trimester.pk}')

    # --- role branching ----------------------------------------------------

    def test_only_a_professor_gets_the_write_actions(self):
        response = self.as_(self.professor).get(self.url())

        self.assertContains(
            response, f'/student/{self.student.pk}/grade/new/')
        self.assertContains(
            response, f'/student/{self.student.pk}/ausencia/new/')
        self.assertContains(response, f'/student/edit/grade/{self.grade.pk}/')
        self.assertContains(
            response, f'/student/edit/ausencia/{self.ausencia.pk}/')

    def test_a_student_reaches_no_write_route_and_no_editar_link(self):
        response = self.as_(self.pupil).get('/student/')

        # The page did render for them — otherwise the assertions below pass
        # vacuously on an empty or forbidden response.
        self.assertContains(response, 'Mala nota')
        self.assertNotContains(response, 'Editar')
        self.assertNotContains(response, 'grade/new/')
        self.assertNotContains(response, 'ausencia/new/')
        self.assertNotContains(response, '/student/edit/')

    def test_a_tutor_reaches_no_write_route_and_no_editar_link(self):
        response = self.as_(self.tutor).get('/student/?child=0')

        self.assertContains(response, 'Mala nota')
        self.assertNotContains(response, 'Editar')
        self.assertNotContains(response, 'grade/new/')
        self.assertNotContains(response, 'ausencia/new/')
        self.assertNotContains(response, '/student/edit/')

    def test_each_role_gets_its_own_csv_endpoint(self):
        """Three roles, three different exports — a professor's is scoped to
        the student on screen, the other two to the caller."""
        professor = self.as_(self.professor).get(self.url())
        self.assertContains(professor, f'/grades/csv/{self.student.pk}/')
        self.assertContains(professor, 'Descargar notas CSV')

        pupil = self.as_(self.pupil).get('/student/')
        self.assertContains(pupil, 'Descargar mis notas CSV')
        self.assertNotContains(pupil, f'/grades/csv/{self.student.pk}/')

        tutor = self.as_(self.tutor).get('/student/?child=0')
        self.assertContains(tutor, 'Descargar las notas de mis hij@s CSV')
        self.assertNotContains(tutor, f'/grades/csv/{self.student.pk}/')

    # --- the tables --------------------------------------------------------

    def test_zero_means_unnumbered_rather_than_the_first_one(self):
        """`grade_type_number` defaults to 0 and 0 is *unnumbered*. Printing a
        bare 0 beside a 3 would read as an ordinal."""
        import re

        response = self.as_(self.professor).get(self.url())
        body = response.content.decode()

        # The "Nº tipo" cells, in row order: the unnumbered grade says so in
        # words, the numbered one prints its number and never a 0.
        cells = [c.strip() for c in
                 re.findall(r'fig text-\[12\.5px\]\s*\n\s*text-ink-[23]">'
                            r'\s*([^<]*)</div>', body)]
        self.assertIn('único', cells)
        self.assertIn('3', cells)
        self.assertNotIn('0', cells)

    def test_a_failing_grade_is_marked_and_the_locale_is_respected(self):
        """< 5 is a convention this page has always applied; it is carried
        over as `text-bad`, neither invented nor dropped. es-ES renders the
        decimal with a comma on the page — the CSV keeps the dot."""
        response = self.as_(self.professor).get(self.url())

        self.assertContains(response, 'text-bad')
        self.assertContains(response, '2,66')

    def test_the_two_absence_types_do_not_look_alike(self):
        Ausencias.objects.create(
            student=self.student, subject=self.subject,
            trimester=self.trimester, school_year=self.year, Tipo='Ausencia',
            date_time=timezone.now() + timedelta(days=1))
        response = self.as_(self.professor).get(self.url())

        self.assertContains(response, '>Retraso<')
        self.assertContains(response, '>Ausencia<')
        self.assertContains(response, 'bg-bad-dim')

    # --- empty states ------------------------------------------------------

    def test_the_empty_states_span_every_column_they_sit_under(self):
        """The legacy colspans were 7 and 6 against 8 and 6 columns, so a
        professor's empty grades row stopped one column short of its table."""
        response = self.as_(self.professor).get(self.url(
            f'?school_year_id={self.year.pk}'
            f'&trimester_id={self.other_trimester.pk}'))

        self.assertContains(
            response, 'No hay notas para el filtro seleccionado.')
        self.assertContains(
            response, 'No hay ausencias para el filtro seleccionado.')
        self.assertEqual(
            self._table_shapes(response.content.decode()), [(8, 8), (6, 6)])

    def test_a_student_sees_one_column_fewer_and_the_colspans_follow(self):
        """No "Acciones" column for a student, so the same empty rows have to
        span 7 and 5 instead."""
        response = self.as_(self.pupil).get(
            f'/student/?school_year_id={self.year.pk}'
            f'&trimester_id={self.other_trimester.pk}')

        self.assertEqual(
            self._table_shapes(response.content.decode()), [(7, 7), (5, 5)])

    @staticmethod
    def _table_shapes(body):
        """(header columns, empty-row colspan) for each table, in order."""
        import re

        headers = [t.count('<th ')
                   for t in re.findall(r'<thead>(.*?)</thead>', body, re.S)]
        spans = [int(s) for s in re.findall(r'colspan="(\d+)"', body)]
        return list(zip(headers, spans))

    # --- the back link -----------------------------------------------------

    def test_the_back_link_is_scoped_to_the_teachers_own_courses(self):
        """It replaces a `<button data-action="back">`, and the id arrives in
        the URL, so it is resolved against `teacher_courses` rather than
        trusted. A foreign or unparseable id yields no link, not an error."""
        mine = self.as_(self.professor).get(
            self.url(f'?course={self.course.pk}'))
        self.assertContains(
            mine, f'href="/class/{self.course.pk}/dashboard/"')

        theirs = self.as_(self.professor).get(
            self.url(f'?course={self.other_course.pk}'))
        self.assertNotContains(
            theirs, f'/class/{self.other_course.pk}/dashboard/')
        self.assertContains(theirs, 'Alumn@s')

        junk = self.as_(self.professor).get(self.url('?course=abc'))
        self.assertEqual(junk.status_code, 200)
        self.assertContains(junk, 'Alumn@s')


class StudentFileMigrationTests(V2CascadeAssertions, AccessControlTestCase):
    """Stage 2: the student/tutor record page onto the v2 cascade.

    This page is the one migration that is also a bug fix. `student_file.html`
    had no `{% extends %}` at all — a `{% block %}` outside an inheriting
    template is a no-op, so its contents were emitted raw, ahead of any
    doctype. The document that did arrive arrived sideways: the page included
    `student_dashboard_content.html`, which extends `base.html`, and including
    a template that extends another renders the whole extended document
    inline. The response was a complete legacy page nested inside a `<div>`,
    with the page's own `</div>` trailing after `</html>`.

    Both halves are pinned below: the page extends a real base, and it no
    longer pulls a shell-bearing template in through an include.
    """

    URL = '/student/'

    # --- cascade ------------------------------------------------------------

    def assert_v2_cascade_without_the_teacher_shell(self, response):
        """The shell is shared now; what must be absent is the teacher's nav.

        This page first shipped on `base_v2` with an inline header, because
        `base_shell_v2` was the professor's unconditionally and every
        destination in it is `@teacher_required` — a chrome whose every control
        answers 403 for this page's only audience. The shell branches on the
        role now, so `assert_v2_only` applies in full; this adds the part that
        stopped being structural and became a matter of what is rendered.
        """
        self.assert_v2_only(response)
        for teacher_only in ('Mis clases', 'Bachillerato',
                             'Buscar alumn@', '/search/', '/teacher/'):
            self.assertNotContains(response, teacher_only)

    def test_the_student_record_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.pupil).get(self.URL)

        self.assertTemplateUsed(response, 'mainapp/student_file.html')
        self.assert_v2_cascade_without_the_teacher_shell(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_tutor_view_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.tutor).get(self.URL)

        self.assertTemplateUsed(response, 'mainapp/student_file.html')
        self.assert_v2_cascade_without_the_teacher_shell(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    # --- the missing {% extends %} -----------------------------------------

    def test_the_page_is_a_complete_document_for_both_roles(self):
        """The regression that mattered: markup ahead of the doctype."""
        for user in (self.pupil, self.tutor):
            with self.subTest(user=user.username):
                response = self.as_(user).get(self.URL)
                body = response.content.decode()

                self.assertTrue(body.lstrip().startswith('<!doctype html>'))
                self.assertEqual(body.lower().count('<!doctype'), 1)
                self.assertIn('<html lang="es"', body)
                self.assertIn('</html>', body)
                # The wrapper that used to precede the doctype.
                self.assertNotIn('page-content-wrap', body)

    def test_no_shell_bearing_template_arrives_through_the_include(self):
        """`student_dashboard_content.html` extends `base.html`, so including
        it rendered a second full document inside this one. The record is a
        shell-free partial now."""
        for user in (self.pupil, self.tutor):
            with self.subTest(user=user.username):
                response = self.as_(user).get(self.URL)

                self.assertTemplateUsed(response, 'mainapp/_student_record.html')
                self.assertTemplateNotUsed(
                    response, 'mainapp/student_dashboard_content.html')
                self.assertTemplateNotUsed(response, 'base.html')
                self.assertTemplateNotUsed(response, 'navbar.html')
                self.assertTemplateNotUsed(response, 'sidebar.html')

    def test_the_page_titles_itself(self):
        """An empty <title> was the visible half of the missing extends."""
        response = self.as_(self.pupil).get(self.URL)

        self.assertContains(response, f'<title>Ficha · {self.student.Name}')

    # --- the child selector, rebuilt ---------------------------------------

    def test_the_child_selector_is_links_rather_than_a_scripted_select(self):
        """The legacy control was a `<select data-autosubmit>` driven by
        behaviors.js, which `base_v2` does not load. Real hrefs need no
        JavaScript and produce the ?child= index the view already reads."""
        tutor = self._user('tut2', 'tutor',
                           children=[self.student, self.other_student])
        response = self.as_(tutor).get(self.URL)

        self.assertContains(response, '?child=0')
        self.assertContains(response, '?child=1')
        self.assertNotContains(response, 'data-autosubmit')
        self.assertNotContains(response, '<select')

    def test_the_child_links_carry_the_active_filters(self):
        """Switching child must not silently reset the year and trimester."""
        tutor = self._user('tut3', 'tutor',
                           children=[self.student, self.other_student])
        response = self.as_(tutor).get(
            f'{self.URL}?school_year_id={self.year.pk}'
            f'&trimester_id={self.trimester.pk}')

        self.assertContains(
            response,
            f'?child=1&amp;school_year_id={self.year.pk}'
            f'&amp;trimester_id={self.trimester.pk}')

    def test_a_student_gets_no_child_selector(self):
        response = self.as_(self.pupil).get(self.URL)

        self.assertNotContains(response, '?child=')
        self.assertNotContains(response, 'Alumn@s a tu cargo')

    def test_a_tutor_with_no_children_gets_a_message_not_empty_controls(self):
        """The view returns early here with neither a student nor a year list,
        so the record and its filter bar would render as empty controls."""
        tutor = self._user('tut4', 'tutor')
        response = self.as_(tutor).get(self.URL)

        self.assertContains(response, 'Sin alumn@s asociad@s')
        self.assertTemplateNotUsed(response, 'mainapp/_student_record.html')
        self.assert_no_leaked_template_comments(response)

    # --- scoping: unchanged by the migration -------------------------------

    def test_a_student_sees_their_own_record(self):
        response = self.as_(self.pupil).get(self.URL)

        self.assertContains(response, self.student.Name)
        self.assertNotContains(response, self.other_student.Name)

    def test_a_tutor_sees_the_selected_child(self):
        tutor = self._user('tut5', 'tutor',
                           children=[self.student, self.other_student])

        first = self.as_(tutor).get(f'{self.URL}?child=0')
        self.assertContains(first, f'>{self.student.Name}</h1>')

        second = self.as_(tutor).get(f'{self.URL}?child=1')
        self.assertContains(second, f'>{self.other_student.Name}</h1>')

    def test_a_tutor_cannot_reach_a_child_who_is_not_theirs(self):
        """`?child=` is an index into this tutor's own `profile.children`, and
        the view clamps it. Out of range, negative and non-numeric must all
        fall back to their own first child rather than reaching anyone else."""
        for raw in ('1', '99', '-3', 'abc', ''):
            with self.subTest(child=raw):
                response = self.as_(self.tutor).get(f'{self.URL}?child={raw}')

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, self.student.Name)
                self.assertNotContains(response, self.other_student.Name)

    def test_a_professor_cannot_reach_the_student_record_page(self):
        response = self.as_(self.professor).get(self.URL)

        self.assertEqual(response.status_code, 403)

    def test_an_anonymous_caller_is_redirected(self):
        self.assertEqual(self.client.get(self.URL).status_code, 302)


class LegacyCascadeTeardownTests(TestCase):
    """Every hand-written stylesheet is gone, and they did not go together.

    The plan read as though the four died with `base.html`. Three did.
    `global-styles.css` outlived it by four templates: the `adminage/` pages
    never extended `base.html` — each was its own `<!DOCTYPE>` — so no
    `{% extends %}` sweep ever listed them, and they linked that sheet
    directly. Deleting it early would have unstyled the administrator flows
    silently, and only for administrators. It went when its last consumer,
    `assign_subjects.html`, migrated.
    """

    TEMPLATES = pathlib.Path(settings.BASE_DIR)
    GONE = (
        'templates/base.html',
        'templates/navbar.html',
        'templates/sidebar.html',
        'static/css/navbar.css',
        'static/css/sidebar.css',
        'static/css/site-pages.css',
        'static/css/global-styles.css',
        'static/js/behaviors.js',
        # Born unreachable, not superseded: added in the same commit as the
        # working reassign_students.html, with no view rendering it and both
        # its AJAX endpoints undefined. `ajax_load_courses_for_filter` has
        # never appeared in urls.py in any commit.
        'mainapp/templates/adminage/modify_assignments.html',
        # Also dead from birth: added by `bace44a` and never linked by
        # anything, in any commit.
        'static/css/simple-layout.css',
        # The last legacy page. Unlike the others it *worked* — its inline
        # <script> carried a CSP nonce — so this one is a rebuild rather than a
        # repair. It also moved into `adminage/`, where every other
        # administrator flow already lived.
        'mainapp/templates/reassign_students.html',
    )

    def test_the_legacy_cascade_is_gone(self):
        for relative in self.GONE:
            with self.subTest(path=relative):
                self.assertFalse((self.TEMPLATES / relative).exists())

    def test_nothing_extends_base_html_any_more(self):
        """A stray `{% extends "base.html" %}` would now be a
        TemplateDoesNotExist at render time rather than a wrong-looking page,
        but only on the route that renders it. This states it once."""
        for root in ('templates', 'mainapp/templates'):
            for path in (self.TEMPLATES / root).rglob('*.html'):
                with self.subTest(template=path.name):
                    self.assertNotIn('extends "base.html"', path.read_text())
                    self.assertNotIn("extends 'base.html'", path.read_text())

    def test_no_template_links_a_deleted_stylesheet(self):
        """Asserts on the `{% static %}` link, not on the filename appearing
        somewhere in the file.

        The previous version of this check was a raw substring search, and it
        went on passing after `assign_subjects.html` dropped its link — because
        the `{% comment %}` block explaining the removal contains the words
        "global-styles.css". A comment satisfied a test about a stylesheet
        link. Naming a dead file in prose is fine; linking it is not, so match
        the thing that actually loads it.
        """
        dead = ('navbar.css', 'sidebar.css', 'site-pages.css',
                'global-styles.css', 'simple-layout.css', 'login.css')

        for root in ('templates', 'mainapp/templates'):
            for path in (self.TEMPLATES / root).rglob('*.html'):
                text = path.read_text()
                for sheet in dead:
                    with self.subTest(template=path.name, sheet=sheet):
                        self.assertNotIn(f"static 'css/{sheet}'", text)
                        self.assertNotIn(f'static "css/{sheet}"', text)

    def test_tailwind_is_the_only_stylesheet_left_on_disk(self):
        """`src/` holds the Tailwind source; `tailwind.css` is its build."""
        present = sorted(
            p.name for p in (self.TEMPLATES / 'static/css').iterdir()
            if p.suffix == '.css')

        self.assertEqual(present, ['tailwind.css'])

    # `base_v2.html` is the root of the cascade and so extends nothing by
    # definition. The four `_`-prefixed files are partials, and carry no
    # `{% extends %}` on purpose: including a template that extends another
    # renders the *entire* extended document inline, which is how
    # `student_file.html` once emitted a second complete <html> inside a div.
    CASCADE_EXEMPT = ('base_v2.html', '_class_scope.html', '_course_dependents.html',
                      '_student_record.html', '_trimester_options.html')

    def test_every_template_is_on_the_v2_cascade(self):
        """The overhaul's closing assertion: no page is its own document.

        A standalone document is exactly what kept the eight `adminage/` pages
        out of the page count for the whole of stage 2 — no `{% extends %}`
        sweep listed them, because they extended nothing. This states the
        invariant directly instead, so the next one cannot hide the same way.

        It asserts on `{% extends %}` alone and deliberately does not also
        search for the string `<!DOCTYPE`. The first draft did, and failed on
        two already-migrated pages whose `{% comment %}` blocks *describe* the
        doctype they dropped — the same way the old `global-styles.css` guard
        went on passing against a comment. Django requires `{% extends %}` to
        be the first tag in the file, so it is the assertion with the effect.
        """
        for root in ('templates', 'mainapp/templates'):
            for path in (self.TEMPLATES / root).rglob('*.html'):
                if path.name in self.CASCADE_EXEMPT:
                    continue
                with self.subTest(template=path.name):
                    self.assertRegex(
                        path.read_text(),
                        r'{%\s*extends\s+["\']base_(shell_)?v2\.html["\']')

    def test_no_migrated_page_reaches_for_behaviors_js(self):
        """It is deleted, so a reference is now a 404 rather than a hook that
        merely does nothing. Comments naming it are fine; a `<script src>` is
        not."""
        for root in ('templates', 'mainapp/templates'):
            for path in (self.TEMPLATES / root).rglob('*.html'):
                with self.subTest(template=path.name):
                    self.assertNotIn("js/behaviors.js'", path.read_text())


class ConfirmedBugRegressionTests(TransactionTestCase):
    """The eight failure modes found by reading the routes, one class.

    A `TransactionTestCase` rather than the usual `AccessControlTestCase`,
    and that is load-bearing for exactly one test in here. `ATOMIC_REQUESTS`
    is unset, so production runs in autocommit: a duplicate row raises
    `IntegrityError` and the *next* insert in the same loop still works.
    `TestCase` wraps each test in an atomic block, where the first
    `IntegrityError` poisons the transaction and every later statement raises
    `TransactionManagementError` instead — so the partial-batch test would
    describe a failure production cannot produce. The rest of the class rides
    along rather than being split off, since a second fixture set is the more
    expensive kind of duplication.

    Fixtures are built in `setUp`: `setUpTestData` is a `TestCase` facility
    and does nothing useful without the class-wide transaction.
    """

    def setUp(self):
        self.year = School_year.objects.create(year='2025-2026')
        self.trimester = Trimester.objects.create(Name=1, school_year=self.year)
        self.course = Course.objects.create(
            Tipo='Eso', Section='1A', school_year=self.year)

        self.student = Students.objects.create(
            Name='Ana Lopez', Email='ana@example.com')
        self.second_student = Students.objects.create(
            Name='Beto Ruiz', Email='beto@example.com')
        for student in (self.student, self.second_student):
            Students_Courses.objects.create(
                student=student, course_section=self.course)

        self.subject = Subjects.objects.create(Name='Matematicas')
        self.teacher = Teachers.objects.create(Name='Profesora A')
        Subjects_Courses.objects.create(
            subject=self.subject, teacher=self.teacher, course=self.course,
            trimester=self.trimester)

        self.professor = self._user('prof1', 'professor', teacher=self.teacher)
        self.unlinked_professor = self._user('prof0', 'professor')
        # The two states the denial bugs live in: a student account whose
        # `student` FK was never filled, and a role no branch recognises.
        # Both are reachable — roles are assigned by hand in Django admin.
        self.unlinked_pupil = self._user('alum0', 'student')
        self.oddball = self._user('raro1', 'desconocido')

    def _user(self, username, role, student=None, teacher=None):
        user = User.objects.create_user(username, password=PW)
        Profile.objects.create(
            user=user, role=role, student=student, teacher=teacher)
        return user

    def as_(self, user):
        self.client.force_login(user)
        return self.client

    # --- 1. A view rendering a template that has never existed -------------

    def test_a_student_with_no_student_row_gets_a_403_not_a_missing_template(self):
        """`student_detail` rendered `mainapp/student_profile.html`, which has
        never been committed on any branch, so the route answered 500 with a
        TemplateDoesNotExist. `forbidden.html` already says why access was
        refused and names the account, which is what an administrator needs in
        order to fix it."""
        response = self.as_(self.unlinked_pupil).get('/student/')

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'forbidden.html')
        self.assertContains(response, 'ficha de estudiante', status_code=403)
        self.assertContains(response, 'alum0', status_code=403)

    # --- 2. Denials returned as HTTP 200 ----------------------------------

    def test_login_refuses_an_unroutable_role_with_403_on_get(self):
        """An authenticated GET of `/` with a role no branch recognises. It
        rendered forbidden.html at 200 — the failure mode this module's
        docstring says already shipped once."""
        response = self.as_(self.oddball).get('/')

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'forbidden.html')

    def test_login_refuses_an_unroutable_role_with_403_on_post(self):
        """Same denial down the POST branch: the password was right and the
        account is still refused, which is a denial like any other."""
        response = self.client.post(
            '/', {'username': 'raro1', 'password': PW})

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'forbidden.html')

    def test_the_csv_route_refuses_with_403_and_not_a_downloadable_denial(self):
        """The worst of the three: `grades_csv` answers `text/csv`, so a 200
        carrying an HTML denial body is a file the browser saves to disk."""
        response = self.as_(self.unlinked_pupil).get('/grades/csv/')

        self.assertEqual(response.status_code, 403)
        self.assertNotIn('text/csv', response['Content-Type'])

    # --- 3. One bad Section broke every professor's dashboard -------------

    def test_the_section_sort_key_is_total(self):
        """`Section` is unvalidated on the way in — `main_course_name` is a
        hidden field concatenated straight into it, and `Course` is registered
        raw in Django admin. The key read `section[0]` and `section[1]`, so one
        bad row raised ValueError or IndexError for every professor, on a page
        they had no way to diagnose."""
        from .views import sort_key_section

        for section in ('A1', '9', '10', '', 'Eso 1', '1A'):
            with self.subTest(section=section):
                course = Course(Tipo='Eso', Section=section)
                self.assertIsInstance(sort_key_section(course), tuple)

    def test_ten_sorts_after_nine_not_before_it(self):
        """The single-character slice read '10' as (1, '0'), which sorts it
        between '1A' and '2A'. It never raised, so it was the one of the three
        that was silently wrong rather than loud."""
        from .views import sort_key_section

        courses = [Course(Tipo='Eso', Section=s) for s in ('10A', '9A', '2A')]

        ordered = [c.Section for c in sorted(courses, key=sort_key_section)]

        self.assertEqual(ordered, ['2A', '9A', '10A'])

    def test_the_dashboard_survives_a_malformed_section(self):
        """The end of the same story: a section an administrator can create
        today must not 500 a different role's landing page."""
        Course.objects.create(Tipo='Eso', Section='A1', school_year=self.year)
        Subjects_Courses.objects.create(
            subject=self.subject, teacher=self.teacher, trimester=self.trimester,
            course=Course.objects.get(Section='A1'))

        response = self.as_(self.professor).get('/teacher/')

        self.assertEqual(response.status_code, 200)

    # --- 4. Non-numeric ids answered 500 ----------------------------------

    def test_non_numeric_ids_are_bad_filters_not_server_errors(self):
        """`except Model.DoesNotExist` does not catch what a non-numeric
        primary key raises — Django's integer coercion raises `ValueError`. The
        same file already guards this in `section_courses`,
        `student_dashboard_content` and `grades_csv`."""
        client = self.as_(self.professor)

        for url in ('/teacher/?school_year=abc',
                    '/search/?q=Ana&course=abc',
                    '/ajax/load-trimesters/?school_year=abc',
                    '/ajax/load-trimesters/?school_year_id=abc'):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_the_trimester_fragment_forces_evaluation_inside_its_guard(self):
        """`load_trimesters` is the odd one: its queryset was returned lazily,
        so the ValueError landed during template rendering, past any guard
        around the `.filter()` call. It has to be evaluated inside the guard —
        the shape `_course_levels` already uses."""
        response = self.as_(self.professor).get(
            '/ajax/load-trimesters/?school_year=abc')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(self.trimester.pk))

    def test_the_class_csv_download_survives_a_non_numeric_filter(self):
        """POST-side twin of the above, and lazy in the same way: the filtered
        queryset is only walked while the CSV is being written."""
        response = self.as_(self.professor).post(
            f'/class/{self.course.pk}/grades/download/', {'subject': 'abc'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    # --- 5. A partly-failed bulk absence reported as success --------------

    def _absence_post(self, students, when='2026-05-01T10:00'):
        return {
            'students': [s.pk for s in students],
            'subject': self.subject.pk,
            'trimester': self.trimester.pk,
            'school_year': self.year.pk,
            'Tipo': 'Ausencia',
            'date_time': when,
        }

    def test_a_partly_failed_bulk_absence_names_who_was_skipped(self):
        """`except Exception: continue` reported "creadas para 1" and stopped
        there. At thirty students, "creadas para 28" gives a teacher no way to
        find the other two.

        The colliding row is seeded *through the form*, not the ORM. `Ausencias`
        is unique on (student, subject, trimester, date_time) and `USE_TZ` is on
        with `TIME_ZONE='Europe/Madrid'`, so a row from `timezone.now()` and one
        parsed from a naive form string are different instants — an ORM-seeded
        duplicate would not collide at all.
        """
        url = f'/class/{self.course.pk}/dashboard/'
        client = self.as_(self.professor)
        client.post(url, self._absence_post([self.student]), follow=True)
        self.assertEqual(Ausencias.objects.count(), 1)

        response = client.post(
            url, self._absence_post([self.student, self.second_student]),
            follow=True)

        self.assertEqual(Ausencias.objects.count(), 2)
        body = response.content.decode()
        self.assertIn('Ausencias creadas para 1 estudiante(s).', body)
        self.assertIn('Ya existía esa ausencia', body)
        self.assertIn(self.student.Name, body)

    def test_a_wholly_duplicate_batch_says_duplicate_rather_than_guessing(self):
        """Total failure was already reported, but as "(posibles duplicados)" —
        a guess at a cause the exception already names."""
        url = f'/class/{self.course.pk}/dashboard/'
        client = self.as_(self.professor)
        client.post(url, self._absence_post([self.student]), follow=True)

        response = client.post(
            url, self._absence_post([self.student]), follow=True)

        body = response.content.decode()
        self.assertIn('Ya existía esa ausencia para 1 estudiante(s)', body)
        self.assertNotIn('Ausencias creadas para', body)
        self.assertNotIn('posibles duplicados', body)

    # --- 6. The one professor route that did not fail closed --------------

    def test_the_trimester_endpoint_requires_a_teachers_link(self):
        """It was `@role_required('professor')` while every other professor
        route is `@teacher_required` — the single cell of the role x route
        matrix that answered 200 to an unlinked account."""
        response = self.as_(self.unlinked_professor).get(
            f'/ajax/load-trimesters/?school_year={self.year.pk}')

        self.assertEqual(response.status_code, 403)

    # --- 7. Logout acted on GET -------------------------------------------

    def test_a_cross_site_get_can_no_longer_sign_anyone_out(self):
        """`<img src="/logout/">` on any page cleared the session: a GET
        carries no CSRF token, so there was nothing to check. Django's own
        LogoutView has been POST-only since 4.1."""
        client = self.as_(self.professor)

        response = client.get('/logout/', HTTP_REFERER='https://evil.example')

        self.assertEqual(response.status_code, 405)
        self.assertEqual(client.get('/teacher/').status_code, 200)

    def test_the_shell_signs_out_with_a_post_and_a_token(self):
        """The only logout control in the app. It has to keep working, and it
        has to keep looking the same — hence a button styled as the link was."""
        response = self.as_(self.professor).get('/teacher/')

        self.assertContains(response, 'action="/logout/" method="post"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, '>Salir</button>')

        self.assertEqual(self.client.post('/logout/').status_code, 302)
        self.assertEqual(self.client.get('/teacher/').status_code, 302)

    # --- 8. The 429 body ---------------------------------------------------

    def test_the_429_body_is_a_spanish_page_and_not_a_bare_string(self):
        """It answered `Too many requests.` as text/html. Rendered off
        `base_v2`, not `base_shell_v2`: some limits are keyed by IP, so an
        anonymous caller reaches this and the shell's nav branches on a role
        such a caller does not have."""
        from django.test import RequestFactory
        from django_ratelimit.exceptions import Ratelimited

        from .middleware import RatelimitTo429Middleware

        request = RequestFactory().get('/grades/csv/')
        middleware = RatelimitTo429Middleware(lambda r: None)

        response = middleware.process_exception(request, Ratelimited())

        self.assertEqual(response.status_code, 429)
        body = response.content.decode()
        self.assertIn('Demasiadas peticiones', body)
        self.assertIn('css/tailwind.css', body)
        self.assertNotIn('Too many requests', body)


class RoleAwareShellTests(V2CascadeAssertions, AccessControlTestCase):
    """`base_shell_v2` branches on the role, and that is not cosmetic.

    It used to render the professor's nav to everyone. "Mis clases", the three
    section links and the search box are all `@teacher_required`, so a student
    or a tutor extending the shell got a chrome whose every control answers
    403. `student_file.html` sidestepped it with an inline header of its own;
    the administrator flows would have been a third copy.

    The nav is also the only place in the app that states what a role may do,
    so a link leaking across roles is worth a test even though the view behind
    it would refuse anyway.
    """

    TEACHER_NAV = ('Mis clases', 'Bachillerato', '/teacher/', '/section/eso/')
    ADMIN_NAV = ('Administración', '/adminage/', 'Reasignar alumn@s')
    STUDENT_NAV = ('/student/',)

    def test_a_professor_gets_the_teaching_nav_and_nothing_elses(self):
        response = self.as_(self.professor).get('/teacher/')

        for entry in self.TEACHER_NAV:
            self.assertContains(response, entry)
        for entry in self.ADMIN_NAV:
            self.assertNotContains(response, entry)

    def test_a_student_gets_no_teaching_nav_and_no_search_box(self):
        """`search_students` is @teacher_required and scoped to the teacher's
        own students — not a search this account has any use for."""
        response = self.as_(self.pupil).get('/student/')

        for entry in self.TEACHER_NAV:
            self.assertNotContains(response, entry)
        for entry in self.ADMIN_NAV:
            self.assertNotContains(response, entry)
        self.assertNotContains(response, 'Buscar alumn@')
        self.assertContains(response, '/student/')

    def test_a_tutor_gets_the_same_nav_as_a_student(self):
        """One destination serves both: `student_dashboard` decides from the
        profile whose record to show. The child picker belongs to the page —
        the nav does not know how many children the account has."""
        response = self.as_(self.tutor).get('/student/')

        for entry in self.TEACHER_NAV:
            self.assertNotContains(response, entry)
        self.assertContains(response, 'Seguimiento')
        self.assertContains(response, '/student/')

    def test_every_role_can_sign_out(self):
        """There was no logout control in this shell at all — the legacy
        navbar had one and it did not survive the rebuild."""
        for user, url in ((self.professor, '/teacher/'),
                          (self.pupil, '/student/'),
                          (self.tutor, '/student/')):
            with self.subTest(user=user.username):
                response = self.as_(user).get(url)

                self.assertContains(response, '/logout/')
                self.assertContains(response, 'Salir')

    def test_the_identity_block_names_a_student_not_just_a_username(self):
        """`firstof` read only `profile.teacher.Name`, so two of the four roles
        fell through to a bare username."""
        response = self.as_(self.pupil).get('/student/')

        self.assertContains(response, self.student.Name)

    def test_the_403_page_offers_the_nav_the_account_does_have(self):
        """`forbidden.html` extends this shell, so a denied account keeps its
        own nav there — not the one it was just refused. The 403 is where
        someone most needs a way back to where they are allowed to be."""
        response = self.as_(self.pupil).get('/teacher/')

        self.assertEqual(response.status_code, 403)
        for entry in self.TEACHER_NAV + self.ADMIN_NAV:
            self.assertNotContains(response, entry, status_code=403)
        self.assertContains(response, '/student/', status_code=403)
        self.assertContains(response, '/logout/', status_code=403)

    def test_the_administrator_branch_renders(self):
        """All eight administrator templates are still standalone documents
        that extend nothing, so no admin page reaches this shell yet. The one
        route that does is the 403 — which is enough to keep the branch from
        rotting silently before stage 3 gets to it."""
        response = self.as_(self.admin).get('/teacher/')

        self.assertEqual(response.status_code, 403)
        for entry in self.ADMIN_NAV:
            self.assertContains(response, entry, status_code=403)
        for entry in self.TEACHER_NAV:
            self.assertNotContains(response, entry, status_code=403)

    def test_the_nav_marks_the_page_you_are_on_for_a_student_too(self):
        """Active state is read off the resolved URL, so a page gets it by
        existing at that route rather than by a view passing a flag."""
        response = self.as_(self.pupil).get('/student/')

        self.assertContains(response, 'aria-current="page"')


class AdminDashboardV2Tests(V2CascadeAssertions, AccessControlTestCase):
    """The first administrator page on the v2 cascade (stage 3).

    `adminage_dashboard.html` was a standalone <!DOCTYPE> with a 157-line
    inline <style> block — the one still carrying the
    `/* ... (TUS ESTILOS CSS COMPLETOS AQUÍ) ... */` placeholder. It now
    extends `base_shell_v2`, which makes it the first page to reach that
    shell's administrator branch for real rather than via forbidden.html.

    The migration's substantive decision is subtractive: four of the page's
    action buttons were, one for one, four of the six entries in the nav
    beside it, so they are gone. What is left is the school-year list, which
    is the one thing the nav cannot express — see
    `test_the_year_rows_are_the_only_working_route_into_course_creation`.
    """

    URL = '/adminage/'

    # The four legacy button labels. Every destination behind them is a nav
    # entry now, so their absence is what "not a second copy of the menu"
    # means concretely.
    LEGACY_BUTTONS = (
        'Iniciar: Crear y Configurar Nuevo Año Escolar',
        'Crear y Asignar Estudiante a Clase',
        'Reasignar Estudiantes de Clase',
        'Asignar Asignaturas a Cursos',
    )

    def test_the_panel_is_built_on_the_v2_cascade_only(self):
        response = self.as_(self.admin).get(self.URL)

        self.assertTemplateUsed(response, 'adminage/adminage_dashboard.html')
        self.assert_v2_only(response)

    def test_the_standalone_document_and_its_inline_stylesheet_are_gone(self):
        """The page used to ship its own <head> and its own :root palette.
        A second <html> inside the shell's would be quirks mode, silently."""
        response = self.as_(self.admin).get(self.URL)
        html = response.content.decode()

        self.assertEqual(html.lower().count('<!doctype'), 1)
        self.assertEqual(html.lower().count('<html'), 1)
        self.assertNotIn('<style', html.lower())
        self.assertNotIn('TUS ESTILOS CSS COMPLETOS', html)

    def test_its_scripts_are_self_hosted_and_it_needs_none_of_its_own(self):
        response = self.as_(self.admin).get(self.URL)

        self.assert_scripts_are_self_hosted(response)

    def test_it_carries_no_inert_hooks_and_leaks_no_template_comments(self):
        """A prior slice shipped a two-line `{# #}` as visible page text past
        a fully green suite."""
        response = self.as_(self.admin).get(self.URL)

        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_an_administrator_reaches_it_and_no_other_role_does(self):
        """`@role_required('administrator')` is unchanged by the migration."""
        self.assertEqual(self.as_(self.admin).get(self.URL).status_code, 200)
        for user in (self.professor, self.pupil, self.tutor):
            with self.subTest(user=user.username):
                self.assertEqual(
                    self.as_(user).get(self.URL).status_code, 403)

    def test_the_nav_marks_the_panel_as_the_page_you_are_on(self):
        """Active state comes from `request.resolver_match`, so the page gets
        it by existing at this route — no view passes a flag."""
        response = self.as_(self.admin).get(self.URL)

        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, 'Administración')

    def test_it_lists_the_school_years_the_view_actually_passes(self):
        response = self.as_(self.admin).get(self.URL)

        self.assertContains(response, self.year.year)

    def test_the_year_rows_are_the_only_working_route_into_course_creation(self):
        """`create_courses_sections_view` requires ?school_year_id= and
        redirects straight back here when it is missing (views.py:1721), so
        the nav's unparameterised "Cursos" entry cannot reach it. These rows
        carry the param; that is why the list is not redundant with the nav."""
        response = self.as_(self.admin).get(self.URL)

        self.assertContains(
            response, f'/adminage/create-courses/?school_year_id={self.year.pk}')
        self.assertContains(
            response,
            f'/adminage/assign-subjects/?school_year_id={self.year.pk}')

    def test_it_does_not_restate_the_nav(self):
        """The four action buttons are gone, and the two destinations with no
        per-year form appear exactly once in the response — in the nav."""
        response = self.as_(self.admin).get(self.URL)
        html = response.content.decode()

        for label in self.LEGACY_BUTTONS:
            with self.subTest(label=label):
                self.assertNotContains(response, label)
        self.assertEqual(html.count('/adminage/create-student-class/'), 1)
        self.assertEqual(html.count('/reassign-students/'), 1)

    def test_only_the_newest_year_is_marked_and_it_is_the_one_assign_falls_back_to(self):
        """Not a status field — School_year has none. The marker is the
        ordering stated: the view passes order_by('-year'), and
        `assign_subjects_view` defaults to School_year.objects.order_by(
        '-year').first() when given no param (views.py:1845), which is that
        same first row."""
        newer = School_year.objects.create(year='2099-2100')

        response = self.as_(self.admin).get(self.URL)
        html = response.content.decode()

        self.assertEqual(html.count('Más reciente'), 1)
        # The marker sits in the newest row, not the fixture's older one.
        newest_row = html.index(newer.year)
        self.assertLess(newest_row, html.index('Más reciente'))
        self.assertLess(html.index('Más reciente'), html.index(self.year.year))

    def test_the_empty_state_offers_the_one_action_that_unblocks(self):
        """Nothing in the administrator flows works before a school year
        exists — both per-year links need one — so the empty table names the
        step rather than being a dead end. It is the single nav destination
        this page deliberately repeats."""
        School_year.objects.all().delete()

        response = self.as_(self.admin).get(self.URL)

        self.assertContains(response, 'No hay años escolares dados de alta')
        self.assertContains(response, '/adminage/create-school-year/')

    def test_the_headings_stay_spanish_and_the_english_title_stays_ignored(self):
        """The view passes 'School Admin Dashboard'; LANGUAGE_CODE is es-es
        and the legacy template ignored it too."""
        response = self.as_(self.admin).get(self.URL)

        self.assertNotContains(response, 'School Admin Dashboard')
        self.assertContains(response, '<title>Panel de administración</title>')


class AdminCascadePagesV2Tests(V2CascadeAssertions, AccessControlTestCase):
    """`create_and_assign_student` and `assign_subjects`, and the endpoint
    they used to share.

    These were the jQuery AJAX-cascade pages. `load_course_sections` returned
    JSON that jQuery turned into <option>s; with `behaviors.js` deleted and
    the CSP forbidding inline handlers, none of that could survive a v2
    migration untouched. The endpoint now renders markup that htmx swaps, the
    way `ajax_load_trimesters` does for the trimester cascade.

    Three things the trimester conversion had to get right are pinned here
    too: the page works before any JavaScript runs, no control depends on
    script to be usable, and the server-rendered first paint and the swapped
    fragment come from one renderer so their option text cannot drift.
    """

    STUDENT_URL = '/adminage/create-student-class/'
    ASSIGN_URL = '/adminage/assign-subjects/'
    CASCADE_URL = '/ajax/load-sections/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A subject with no existing assignment, so the POST test asserts a
        # creation rather than re-finding the fixture's own Subjects_Courses.
        cls.fresh_subject = Subjects.objects.create(Name='Historia')

    # --- both pages, as documents -------------------------------------------

    def test_both_pages_are_built_on_the_v2_cascade_only(self):
        for url, template in (
                (self.STUDENT_URL, 'adminage/create_and_assign_student.html'),
                (self.ASSIGN_URL, 'adminage/assign_subjects.html')):
            with self.subTest(url=url):
                response = self.as_(self.admin).get(url)

                self.assertTemplateUsed(response, template)
                self.assert_v2_only(response)
                self.assert_scripts_are_self_hosted(response)
                self.assert_no_inert_js_hooks(response)
                self.assert_no_leaked_template_comments(response)

    def test_neither_page_carries_its_own_document_any_more(self):
        """`create_and_assign_student` was the app's only light-themed page;
        `assign_subjects` was the last template linking global-styles.css."""
        for url in (self.STUDENT_URL, self.ASSIGN_URL):
            with self.subTest(url=url):
                body = self.as_(self.admin).get(url).content.decode()

                self.assertEqual(body.lower().count('<!doctype'), 1)
                self.assertEqual(body.lower().count('<html'), 1)
                self.assertNotIn('<style', body.lower())
                self.assertNotIn('global-styles.css', body)

    def test_the_jquery_cascade_is_gone_from_both_pages(self):
        """Every part of it violated something: the `<script>` block needed a
        nonce, `window.location.href` in a change handler was a navigation
        pretending to be a widget, and jQuery itself is a second library the
        v2 cascade does not load."""
        for url in (self.STUDENT_URL, self.ASSIGN_URL):
            with self.subTest(url=url):
                body = self.as_(self.admin).get(url).content.decode()

                self.assertNotIn('jquery', body.lower())
                self.assertNotIn('window.location', body)
                self.assertNotIn('$(document)', body)
                self.assertNotIn('csp_nonce', body)

    def test_both_pages_stay_administrator_only(self):
        for url in (self.STUDENT_URL, self.ASSIGN_URL, self.CASCADE_URL):
            for user in (self.professor, self.pupil, self.tutor):
                with self.subTest(url=url, user=user.username):
                    self.assertEqual(
                        self.as_(user).get(url).status_code, 403)

    def test_every_aria_describedby_points_at_something(self):
        """The rule `AdminFlowTemplateTests` pins for the courses flow. A
        control describing an element that does not exist reads identically
        to a sighted user."""
        for url in (self.STUDENT_URL, self.ASSIGN_URL):
            with self.subTest(url=url):
                body = self.as_(self.admin).get(url).content.decode()

                for target in re.findall(r'aria-describedby="([^"]+)"', body):
                    self.assertIn(f'id="{target}"', body)

    # --- Spanish, and where the Spanish lives -------------------------------

    def test_the_student_form_is_spanish_and_carries_the_control_class(self):
        """`StudentCreationForm` had no `labels=`, so Django derived "Name"
        and "Email" from the CapitalCase model fields. Django renders the
        widget, so `ctl` has to be set in forms.py to reach the input."""
        response = self.as_(self.admin).get(self.STUDENT_URL)

        self.assertContains(response, 'Nombre')
        self.assertContains(response, 'Correo electrónico')
        self.assertContains(response, 'class="ctl"')
        self.assertNotContains(response, 'Create Student and Assign Class')
        self.assertNotContains(response, 'Full Student Name')

    def test_the_assignment_form_is_spanish(self):
        response = self.as_(self.admin).get(self.ASSIGN_URL)

        self.assertContains(response, 'Asignatura')
        self.assertContains(response, 'Profesor')
        self.assertNotContains(response, 'Select Subject')
        self.assertNotContains(response, 'Select Professor')
        self.assertNotContains(response, 'Asignar Asignaturas a Clases')

    # --- create_and_assign_student: one select, no script -------------------

    def test_the_class_select_is_the_field_the_view_reads(self):
        """The three cascade selects and the hidden #hidden_course_id a script
        kept in sync are replaced by one select named `course_id` — the name
        the view already read off request.POST."""
        response = self.as_(self.admin).get(self.STUDENT_URL)

        self.assertContains(response, 'name="course_id"')
        self.assertNotContains(response, 'hidden_course_id')
        self.assertContains(response, '<optgroup')
        self.assertContains(response, self.course.Section)
        self.assertContains(response, self.other_course.Section)

    def test_the_submit_button_is_not_disabled(self):
        """It used to start `disabled` and be enabled by a change handler. A
        disabled control is not submitted, and the handler is gone — so the
        page would have been unusable. The view already answers a missing
        course_id with an error."""
        body = self.as_(self.admin).get(self.STUDENT_URL).content.decode()

        self.assertNotIn('disabled>', body.replace('option value="" disabled>', ''))

    def test_creating_a_student_still_enrols_them(self):
        response = self.as_(self.admin).post(self.STUDENT_URL, {
            'Name': 'Nueva Alumna', 'Email': 'nueva@example.com',
            'course_id': self.course.pk})

        self.assertEqual(response.status_code, 302)
        created = Students.objects.get(Email='nueva@example.com')
        self.assertTrue(Students_Courses.objects.filter(
            student=created, course_section=self.course).exists())

    def test_creating_a_student_without_a_class_does_not_create_one(self):
        response = self.as_(self.admin).post(self.STUDENT_URL, {
            'Name': 'Sin Clase', 'Email': 'sinclase@example.com',
            'course_id': ''})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Students.objects.filter(Email='sinclase@example.com').exists())

    # --- the endpoint: markup, not JSON -------------------------------------

    def test_the_endpoint_returns_markup_rather_than_json(self):
        response = self.as_(self.admin).get(
            f'{self.CASCADE_URL}?school_year_id={self.year.pk}&course_type=Eso')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/html'))
        self.assertContains(response, '<option')
        self.assertNotContains(response, '"mode"')

    def test_the_levels_offered_are_only_those_with_a_section_created(self):
        """`MAIN_COURSES` allows Eso 1-4; the fixture has 1A and 2B, so 3 and
        4 must not be offered — a level with nothing behind it is a dead end."""
        response = self.as_(self.admin).get(
            f'{self.CASCADE_URL}?school_year_id={self.year.pk}&course_type=Eso')

        self.assertContains(response, '1º Eso')
        self.assertContains(response, '2º Eso')
        self.assertNotContains(response, '3º Eso')
        self.assertNotContains(response, '4º Eso')

    def test_asking_for_a_level_returns_the_whole_dependent_block(self):
        """One response shape, not two. Changing the type has to repopulate
        Nivel *and* clear Sección, which is two targets for one hx-target —
        so the endpoint returns both selects and the level stays selected."""
        response = self.as_(self.admin).get(
            f'{self.CASCADE_URL}?school_year_id={self.year.pk}'
            f'&course_type=Eso&level=1')
        body = response.content.decode()

        self.assertIn('id="id_course_level"', body)
        self.assertIn('id="id_course_section"', body)
        self.assertIn('<option value="1" selected>1º Eso</option>', body)

        # Scoped to the section select: the level select legitimately carries
        # a `value="2"` of its own, for "2º Eso".
        sections = body.split('id="id_course_section"', 1)[1]
        self.assertIn(f'value="{self.course.pk}"', sections)
        self.assertIn(self.course.Section, sections)
        # 2B belongs to level 2, so it must not appear under level 1.
        self.assertNotIn(self.other_course.Section, sections)

    def test_a_non_numeric_year_is_an_empty_list_not_a_500(self):
        """The old endpoint wrapped everything in `except Exception` and
        answered 500. A bad id in a query string is a bad request."""
        response = self.as_(self.admin).get(
            f'{self.CASCADE_URL}?school_year_id=abc&course_type=Eso')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Seleccione un nivel')

    def test_the_level_lookup_mode_is_not_reintroduced(self):
        """The old template asked for `LEVEL_LOOKUP` via `course_id_lookup`.
        The view has never had that branch — it was added template-side only
        in 56978a8 — so the parameter must simply be ignored, not revived."""
        response = self.as_(self.admin).get(
            f'{self.CASCADE_URL}?school_year_id={self.year.pk}'
            f'&course_id_lookup={self.course.pk}')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'LEVEL_LOOKUP')

    # --- assign_subjects: usable before any JavaScript runs -----------------

    def test_the_cascade_is_rendered_server_side_from_course_id_alone(self):
        """This is what LEVEL_LOOKUP was supposed to do and never did: arrive
        with only ?course_id= — a bookmark, or this view's own post-POST
        redirect — and the type and level come back from the course row. It
        also means the three selects are correct before htmx has run once."""
        body = self.as_(self.admin).get(
            f'{self.ASSIGN_URL}?school_year_id={self.year.pk}'
            f'&course_id={self.course.pk}').content.decode()

        self.assertIn('<option value="Eso" selected>', body)
        self.assertIn('<option value="1" selected>1º Eso</option>', body)
        self.assertIn(f'<option value="{self.course.pk}" selected>', body)

    def test_the_scope_bar_is_a_real_get_form_with_a_submit(self):
        """The section select used to navigate from a jQuery change handler.
        It stays a navigation — the roster, the trimesters and the POST form's
        hidden course_id all change with it — but an honest one that works
        with JavaScript off."""
        response = self.as_(self.admin).get(self.ASSIGN_URL)

        self.assertContains(response, 'method="get"')
        self.assertContains(response, 'Cargar sección')
        self.assertContains(response, 'hx-get="/ajax/load-sections/"')
        self.assertContains(response, 'hx-target="#course-dependents"')

    def test_the_roster_loads_for_the_selected_section(self):
        response = self.as_(self.admin).get(
            f'{self.ASSIGN_URL}?school_year_id={self.year.pk}'
            f'&course_id={self.course.pk}')

        self.assertContains(response, self.student.Name)
        self.assertContains(response, 'name="student_links_selected"')
        self.assertContains(response, f'value="{self.enrolment.pk}"')
        # Enrolled in the other course, so not on this roster.
        self.assertNotContains(response, self.other_student.Name)

    def test_saving_an_assignment_still_writes_it(self):
        response = self.as_(self.admin).post(self.ASSIGN_URL, {
            'course_id': self.course.pk,
            'school_year_id': self.year.pk,
            'subject': self.fresh_subject.pk,
            'teacher': self.teacher_a.pk,
            'trimesters_selected': [self.trimester.pk],
            'student_links_selected': [self.enrolment.pk],
        })

        self.assertEqual(response.status_code, 302)
        assignment = Subjects_Courses.objects.get(
            subject=self.fresh_subject, course=self.course,
            trimester=self.trimester)
        self.assertEqual(assignment.teacher, self.teacher_a)
        self.assertIn(self.enrolment,
                      assignment.assigned_course_sections.all())

    def test_the_select_all_checkbox_is_gone_and_every_box_starts_checked(self):
        """It was a jQuery change handler with no server meaning. The state it
        existed to produce is the default."""
        body = self.as_(self.admin).get(
            f'{self.ASSIGN_URL}?school_year_id={self.year.pk}'
            f'&course_id={self.course.pk}').content.decode()

        self.assertNotIn('select-all-students', body)
        self.assertEqual(body.count('name="student_links_selected"'),
                         body.count('checked'))


class ReassignStudentsV2Tests(V2CascadeAssertions, AccessControlTestCase):
    """The last page in the app off the v2 cascade, and the only rebuild.

    Every other migration repaired something already broken — a `data-action`
    that behaviors.js was no longer there to bind, a page emitting its own
    markup ahead of any doctype. This one *worked*: its inline <script> carried
    a CSP nonce, so its two cascades and its four private JSON endpoints all
    ran. There was a live feature to regress, which is why the POST contract is
    the thing pinned hardest here — `name="assignments"`, one
    `student_id:course_id` per entry, unchanged, so `ReassignStudentsTests`
    still describes this page.
    """

    URL = '/reassign-students/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A second year with a class in it: the destination list spans years on
        # purpose, because promoting a student is the case that made the
        # wrong-year enrolment bug matter in the first place.
        cls.next_year = School_year.objects.create(year='2026-2027')
        cls.next_class = Course.objects.create(
            Tipo='Eso', Section='2A', school_year=cls.next_year)

    def page(self, query=''):
        return self.as_(self.admin).get(self.URL + query)

    def loaded(self):
        """The page with a class chosen, which is the only state with rows."""
        return self.page(f'?course_id={self.course.pk}')

    def test_the_page_is_built_on_the_v2_cascade_only(self):
        response = self.loaded()

        self.assertTemplateUsed(response, 'adminage/reassign_students.html')
        self.assert_v2_only(response)
        self.assert_scripts_are_self_hosted(response)
        self.assert_no_inert_js_hooks(response)
        self.assert_no_leaked_template_comments(response)

    def test_the_four_private_json_endpoints_are_gone(self):
        """They were this template's alone — no other consumer, in any file.
        `get-students` in particular answered names and e-mail addresses."""
        for path in ('/ajax/get-course-numbers/', '/ajax/get-course-sections/',
                     '/ajax/get-students/', '/ajax/get-destination-courses/'):
            with self.subTest(path=path):
                self.assertEqual(
                    self.as_(self.admin).get(path).status_code, 404)

    def test_no_url_is_hardcoded_any_more(self):
        """This was the only template in the app that reversed no route: its
        fetch() calls carried the paths as string literals, so moving a route
        would have broken it silently rather than at reverse time."""
        body = self.loaded().content.decode()

        self.assertNotIn('/ajax/get-', body)
        self.assertIn('/ajax/load-sections/', body)

    def test_the_origin_cascade_is_the_shared_one(self):
        """Not a second dialect. The old page described a course as type +
        number + letter and reassembled the Section string server-side by
        walking characters; this uses the same partial and the same endpoint as
        assign_subjects."""
        response = self.loaded()

        self.assertTemplateUsed(response, 'adminage/_course_dependents.html')
        self.assertContains(response, 'id="scope-school-year"')
        self.assertContains(response, 'id="course-dependents"')

    def test_arriving_with_only_a_course_id_refills_every_select(self):
        """The job the old template's LEVEL_LOOKUP mode was written for and
        never once did — the view never implemented that branch. Year, type,
        level and section all come off the course row, before htmx runs."""
        response = self.loaded()
        body = response.content.decode()

        self.assertIn(f'<option value="{self.year.pk}" selected>', body)
        self.assertIn('<option value="Eso" selected>', body)
        self.assertIn('<option value="1" selected>', body)
        self.assertIn(f'<option value="{self.course.pk}" selected>', body)

    def test_the_roster_is_rendered_by_the_server(self):
        """It used to arrive as JSON and be written into innerHTML by a
        template literal. With JavaScript off the page showed nothing at all."""
        response = self.loaded()

        self.assertContains(response, 'Ana Lopez')
        self.assertContains(response, 'ana@example.com')
        self.assertNotContains(response, 'Beto Ruiz')

    def test_each_row_offers_the_pair_the_view_actually_reads(self):
        """The POST contract, unchanged: `assignments` carrying
        `student_id:course_id`. The old page reached a fourth endpoint just to
        resolve the course_id and wrote it into a hidden input."""
        body = self.loaded().content.decode()

        self.assertIn('name="assignments"', body)
        self.assertIn(f'value="{self.student.pk}:{self.other_course.pk}"', body)

    def test_an_untouched_row_submits_nothing(self):
        """The default option is empty, and the view skips empty entries — so
        leaving a student alone is not a no-op reassignment counted as a
        success."""
        self.assertContains(self.loaded(), '<option value="">Sin cambios</option>')

    def test_the_destinations_span_school_years(self):
        """Moving a student into next year's class is the case the fixed
        enrolment lookup exists for. A destination list limited to the origin's
        own year would make that unreachable from the UI."""
        body = self.loaded().content.decode()

        self.assertIn(f'value="{self.student.pk}:{self.next_class.pk}"', body)
        self.assertIn('2026-2027 · Eso', body)
        self.assertIn('2025-2026 · Eso', body)

    def test_the_class_they_are_already_in_says_so(self):
        """It stays on the list — the view treats it as a no-op rather than an
        error — but picking it by accident should not look like a move."""
        self.assertContains(self.loaded(), '· actual')

    def test_a_lone_class_admits_there_is_nowhere_to_move_anyone(self):
        """Found by rendering against the live database, which holds exactly
        one course: every select offered `1A · actual` and nothing else. The
        controls look operable and cannot do anything, and nothing on the page
        said which of the two it was."""
        Course.objects.exclude(pk=self.course.pk).delete()

        self.assertContains(self.loaded(), 'No hay ninguna otra clase')

    def test_it_says_no_such_thing_when_a_destination_exists(self):
        """Guard against over-tightening: the ordinary page must not carry a
        warning about a state it is not in."""
        self.assertNotContains(self.loaded(), 'No hay ninguna otra clase')

    def test_every_row_select_has_an_accessible_name(self):
        """One select per student, all with the same `name`, so nothing but an
        accessible name distinguishes them to a screen reader."""
        self.assertContains(
            self.loaded(), 'aria-label="Nueva clase de Ana Lopez"')

    def test_the_two_empty_states_do_not_read_alike(self):
        """An administrator has to tell "no class chosen yet" apart from "this
        class has nobody in it" before deciding what to fix."""
        self.assertContains(self.page(), 'Elija una clase arriba')

        Students_Courses.objects.filter(
            course_section=self.other_course).delete()

        self.assertContains(self.page(f'?course_id={self.other_course.pk}'),
                            'no tiene alumn@s matriculad@s')

    def test_no_roster_means_no_destination_list_is_built(self):
        """Nothing to attach them to, so the query that builds them is skipped
        rather than rendered into a form with no rows."""
        self.assertNotContains(self.page(), 'name="assignments"')

    def test_a_bad_course_id_is_a_warning_rather_than_a_500(self):
        """Both shapes reach the view from a hand-edited URL or a stale
        bookmark: an id naming nothing, and an id that is not a number at all —
        the second raises ValueError out of the ORM if it is not caught."""
        for bad in ('999999', 'no-soy-un-numero'):
            with self.subTest(course_id=bad):
                response = self.page(f'?course_id={bad}')

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'no es válido')

    def test_saving_returns_to_the_class_it_was_posted_from(self):
        """The redirect used to drop the scope, which put the administrator
        back at an empty picker every time. Landing on the origin class again
        is also the only way to see the move: the students that moved are gone
        from the roster."""
        response = self.as_(self.admin).post(self.URL, {
            'school_year_id': str(self.year.pk),
            'course_id': str(self.course.pk),
            'assignments': [f'{self.student.pk}:{self.other_course.pk}'],
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn(f'school_year_id={self.year.pk}', response['Location'])
        self.assertIn(f'course_id={self.course.pk}', response['Location'])

    def test_the_rendered_form_actually_moves_a_student(self):
        """End to end over the shape the page emits, rather than over a payload
        hand-written in a test: the option value is submitted verbatim."""
        option = f'{self.student.pk}:{self.other_course.pk}'
        self.assertContains(self.loaded(), f'value="{option}"')

        self.as_(self.admin).post(self.URL, {
            'school_year_id': str(self.year.pk),
            'course_id': str(self.course.pk),
            'assignments': [option],
        })

        self.enrolment.refresh_from_db()
        self.assertEqual(self.enrolment.course_section_id, self.other_course.pk)

    def test_it_is_still_administrator_only(self):
        for user in (self.professor, self.pupil, self.tutor):
            with self.subTest(user=user.username):
                self.assertEqual(
                    self.as_(user).get(self.URL).status_code, 403)
