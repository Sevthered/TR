"""Access-control tests.

Covers the two failure modes that shipped: endpoints with no authorization at
all, and denials returned as HTTP 200 (indistinguishable from success to any
test or monitor).

Note: these use `force_login`, not `client.login()`. django-axes' backend
requires a `request` argument that `client.login()` does not supply.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
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
    '/ajax/get-course-numbers/?school_year_id=1&course_type=Eso',
    '/ajax/get-course-sections/?school_year_id=1&course_type=Eso&course_number=1',
    '/ajax/get-students/?school_year_id=1&course_type=Eso&course_number=1&section_letter=A',
    '/ajax/get-destination-courses/?school_year_id=1&course_type=Eso&course_number=1&section_letter=A',
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
        response = self.client.get(
            '/ajax/get-students/'
            '?school_year_id=1&course_type=Eso&course_number=1&section_letter=A')

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
        """section_courses.html appends it; the course already fixes the year."""
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
