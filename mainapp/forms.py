from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse_lazy
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester, Subjects_Courses, School_year


class CSVImportForm(forms.Form):
    # Simple form for CSV upload.
    csv_file = forms.FileField()


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['student', 'school_year', 'trimester', 'subject',
                  'grade_type', 'grade_type_number', 'grade', 'comments']

        widgets = {
            'student': forms.HiddenInput(),
            'comments': forms.Textarea(attrs={'rows': 3}),
        }

    # Spanish labels: the model fields are English CapitalCase/snake_case and
    # would otherwise surface untranslated on a Spanish-language page.
    LABELS = {
        'school_year': 'Año escolar',
        'trimester': 'Trimestre',
        'subject': 'Materia',
        'grade_type': 'Tipo de nota',
        'grade_type_number': 'Número',
        'grade': 'Nota',
        'comments': 'Comentarios',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 1. Order school year (newest first)
        self.fields['school_year'].queryset = School_year.objects.all().order_by(
            '-year')

        # 2. Trimesters are fetched over the wire once a school year is picked,
        # so the queryset has to follow whichever year is in play: the
        # submitted one when the form is bound, the instance's on edit, the
        # initial the view pre-selects on a blank create form. Scoping it here
        # is also what stops a crafted POST from pairing a trimester with a
        # school year it does not belong to.
        school_year = None
        if self.is_bound:
            school_year = School_year.objects.filter(
                pk=self.data.get('school_year') or None).first()
        elif self.instance and self.instance.pk:
            school_year = self.instance.school_year
        elif self.initial.get('school_year'):
            # create_edit_grade pre-selects the latest year. Honouring it here
            # is what lets the page render a usable trimester list on the first
            # paint, so the form works with JavaScript off.
            school_year = School_year.objects.filter(
                pk=self.initial['school_year']).first()

        if school_year:
            self.fields['trimester'].queryset = Trimester.objects.filter(
                school_year=school_year
            ).order_by('Name')
        else:
            self.fields['trimester'].queryset = Trimester.objects.none()
            self.fields['trimester'].empty_label = "Seleccione un año escolar"

        # The year is already picked in the select above, so repeating it in
        # every option is noise. This text must stay in step with
        # mainapp/_trimester_options.html, which htmx swaps in on a year change
        # — two renderings of the same list.
        self.fields['trimester'].label_from_instance = (
            lambda t: f"Trimestre {t.Name}")

        # v2 control style plus the htmx cascade, attached here because Django
        # renders these widgets itself. The old page did this with jQuery and
        # a JSON endpoint; base_v2 ships htmx and nothing else.
        for name, label in self.LABELS.items():
            self.fields[name].widget.attrs.setdefault('class', 'ctl')
            self.fields[name].label = label

        self.fields['school_year'].widget.attrs.update({
            'hx-get': str(reverse_lazy('ajax_load_trimesters')),
            'hx-target': '#id_trimester',
            'hx-swap': 'innerHTML',
            'hx-trigger': 'change',
        })


class AusenciaEditForm(forms.ModelForm):
    # Form for editing an existing absence.
    class Meta:
        model = Ausencias
        fields = ['subject', 'trimester', 'school_year', 'Tipo', 'date_time']
        widgets = {
            # <input type="datetime-local"> only accepts an ISO value. Without
            # an explicit format Django renders DATETIME_INPUT_FORMATS[0] of
            # the active locale — '%Y-%m-%d %H:%M:%S' under en-us, '%d/%m/%Y
            # %H:%M:%S' under es — and the browser silently blanks the control,
            # so editing an absence lost its date. Parsing was never the
            # problem: DateTimeField accepts the ISO value the browser posts.
            'date_time': forms.DateTimeInput(
                attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }

    LABELS = {
        'subject': 'Materia',
        'trimester': 'Trimestre',
        'school_year': 'Año escolar',
        'Tipo': 'Tipo',
        'date_time': 'Fecha y hora',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Same reasoning as AusenciaForm: Spanish labels, and `ctl` attached
        # here because Django renders the widget, not the template.
        for name, label in self.LABELS.items():
            self.fields[name].widget.attrs.setdefault('class', 'ctl')
            self.fields[name].label = label


class AusenciaForm(forms.ModelForm):
    # Advanced form for registering absences for multiple students.

    # Extra field for selecting multiple students.
    students = forms.ModelMultipleChoiceField(
        queryset=Students.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': 6, 'class': 'ctl'}),
        required=True,
        label='Estudiante(s)'
    )
    # Extra field for date/time.
    date_time = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'class': 'ctl'},
            # See AusenciaEditForm: the control needs an ISO value, which is
            # not what the locale's first input format is in either language.
            format='%Y-%m-%dT%H:%M'),
        label='Fecha y hora'
    )

    class Meta:
        model = Ausencias
        fields = ['subject', 'trimester', 'school_year', 'Tipo']

    def __init__(self, *args, scope=None, **kwargs):
        """`scope` is a views.ClassScope — the same one the page is reading.

        The form used to be built from the Course alone, so it offered every
        enrolled student and every subject the course has ever been taught,
        while the register beside it showed a roster narrowed by the selected
        subject. Two different answers to "who is in this class" on one screen.

        Taking the scope instead means the choices on offer are the ones the
        page is already claiming: `scope.students` is the register's roster,
        and the subjects are the ones taught to this course *in this
        trimester*. The trimester and subject arrive pre-selected, since the
        page states both — but neither is locked, so an absence for another
        subject in the same trimester is still one submit away.
        """
        super().__init__(*args, **kwargs)

        # The Meta fields render as bare selects, and their labels default to
        # the model's English field names on a Spanish-language page. `ctl` is
        # the v2 control style (see static/css/src/app.css); it is harmless on
        # the legacy pages, which do not define the class.
        meta_labels = {
            'subject': 'Materia',
            'trimester': 'Trimestre',
            'school_year': 'Año escolar',
            'Tipo': 'Tipo',
        }
        for name, label in meta_labels.items():
            self.fields[name].widget.attrs.setdefault('class', 'ctl')
            self.fields[name].label = label

        if scope is not None:
            # The register's roster, whichever of the two rules produced it.
            # When a subject narrows it, this narrows with it — the panel must
            # not offer a student the page has just said is not in the list.
            self.fields['students'].queryset = scope.students

            # Subjects_Courses carries a trimester FK, so the subject set is
            # per trimester. These are exactly the tabs in the scope bar.
            self.fields['subject'].queryset = Subjects.objects.filter(
                pk__in=[sc.subject_id for sc in scope.subjects_courses]
            ).order_by('Name')
            if scope.subject_courses is not None:
                self.fields['subject'].initial = scope.subject_courses.subject_id

            # The school year is fixed by the course, never chosen.
            self.fields['school_year'].queryset = School_year.objects.filter(
                pk=scope.school_year.pk)
            self.fields['school_year'].initial = scope.school_year

            self.fields['trimester'].queryset = Trimester.objects.filter(
                school_year=scope.school_year).order_by('Name')
            if scope.trimester is not None:
                self.fields['trimester'].initial = scope.trimester.pk

        # Set default date/time to now. The widget carries the ISO format, so
        # the datetime goes in as a datetime rather than a pre-formatted
        # string — one place deciding how a datetime is written, not two.
        if 'initial' not in kwargs or 'date_time' not in kwargs.get('initial', {}):
            from django.utils import timezone
            self.fields['date_time'].initial = timezone.localtime(
                timezone.now())


MAIN_COURSES = {
    # Helper dictionary for course creation flow.
    'Eso': [1, 2, 3, 4],
    'Bachillerato': [1, 2],
    'IB': [1, 2],
}


class SchoolYearForm(forms.ModelForm):
    # Form for creating a new School Year.
    #
    # Labels are Spanish and `ctl` is on the widget rather than in the
    # template, for the same reason as GradeForm: Django renders the widget
    # itself, so a class written in the template never reaches it.
    class Meta:
        model = School_year
        fields = ['year']
        labels = {
            'year': 'Año escolar',
        }
        widgets = {
            'year': forms.TextInput(
                attrs={'placeholder': 'p. ej. 2025-2026', 'class': 'ctl'}),
        }


class CourseSectionForm(forms.Form):
    # Base form for dynamic sections (Step 2).
    #
    # `main_course_name` is the level number the row belongs to, and it travels
    # in a hidden input, so it is attacker-controlled like any other POST
    # field. As a bare CharField it accepted anything: `main_course_name=9`
    # created an `Eso 9A` that MAIN_COURSES says cannot exist, and
    # `main_course_name=10` produced a three-character Section against
    # `Course.Section`'s max_length=2 — which `bulk_create` does not validate,
    # so it reached the database as a DataError and a 500. The same string
    # later feeds `sort_key_section`, which is strict about its shape.
    #
    # The level is only meaningful next to a course type, so the type is passed
    # in by the view (`form_kwargs` on the formset) rather than guessed here.

    main_course_name = forms.CharField(widget=forms.HiddenInput())

    display_name = forms.CharField(
        label="",
        required=False,
        widget=forms.TextInput(
            attrs={'readonly': 'readonly', 'class': 'ctl'})
    )

    num_subsections = forms.IntegerField(
        label="Nº de secciones (A, B, C…)",
        min_value=1,
        max_value=26,
        initial=3,
        help_text="Por ejemplo, 3 creará 1A, 1B y 1C.",
        widget=forms.NumberInput(attrs={'class': 'ctl'})
    )

    def __init__(self, *args, **kwargs):
        # Absent — the initial render in `_render_step2`, which validates
        # nothing — the check below simply has no valid set and rejects, which
        # is the right way round for a field nobody ever types into.
        self.course_tipo = kwargs.pop('course_tipo', None)
        super().__init__(*args, **kwargs)

    def clean_main_course_name(self):
        value = self.cleaned_data['main_course_name']
        valid = [str(level)
                 for level in MAIN_COURSES.get(self.course_tipo or '', [])]

        if value not in valid:
            raise forms.ValidationError(
                "El nivel «%(level)s» no existe para este tipo de curso.",
                code='invalid_level', params={'level': value})

        return value


class CourseCreationForm(forms.Form):
    # Main form for course creation (Step 1).

    course_tipo = forms.ChoiceField(
        choices=Course.COURSE_TYPE_CHOICES,
        label="Tipo de curso",
        widget=forms.Select(attrs={'class': 'ctl'})
    )

    school_year = forms.ModelChoiceField(
        queryset=School_year.objects.all().order_by('-year'),
        label="Año escolar",
        required=False,
        widget=forms.Select(attrs={'class': 'ctl'})
    )

    def __init__(self, *args, **kwargs):
        initial_school_year_id = kwargs.pop('initial_school_year_id', None)
        self.course_type_initial = kwargs.pop('course_type_initial', None)

        super().__init__(*args, **kwargs)

        if initial_school_year_id:
            self.fields['school_year'].initial = initial_school_year_id
            self.fields['school_year'].widget.attrs['disabled'] = True

        if self.course_type_initial:
            self.fields['course_tipo'].initial = self.course_type_initial
            self.fields['course_tipo'].widget.attrs['disabled'] = True

    def clean(self):
        cleaned_data = super().clean()

        # Recover disabled school_year field.
        if 'school_year' not in cleaned_data:
            school_year_value = self.fields['school_year'].initial or self.data.get(
                'school_year')

            if school_year_value:
                try:
                    cleaned_data['school_year'] = School_year.objects.get(
                        pk=school_year_value)
                except School_year.DoesNotExist:
                    raise forms.ValidationError(
                        "El año escolar seleccionado no existe.")

        return cleaned_data


class SubjectAssignmentForm(forms.Form):
    # Form for assigning subject and teacher.

    # Spanish labels and `ctl` live here rather than in assign_subjects.html,
    # because Django renders these two widgets itself — a class written into
    # the template would never reach the <select>. Same reasoning as GradeForm.
    subject = forms.ModelChoiceField(
        queryset=Subjects.objects.all().order_by('Name'),
        label="Asignatura",
        empty_label="Seleccione una asignatura",
        widget=forms.Select(attrs={'class': 'ctl'}),
        required=True
    )

    teacher = forms.ModelChoiceField(
        queryset=Teachers.objects.all().order_by('Name'),
        label="Profesor",
        empty_label="Seleccione un profesor",
        widget=forms.Select(attrs={'class': 'ctl'}),
        required=True
    )


class StudentCreationForm(forms.ModelForm):
    # Form for creating a new Student.
    class Meta:
        model = Students
        fields = ['Name', 'Email']
        widgets = {
            'Name': forms.TextInput(attrs={'placeholder': 'Nombre y apellidos'}),
            'Email': forms.EmailInput(attrs={'placeholder': 'correo@ejemplo.com'}),
        }

    # The model fields are CapitalCase English, so with no override Django
    # derives "Name" and "Email" from them and renders an English form on a
    # Spanish page.
    LABELS = {
        'Name': 'Nombre',
        'Email': 'Correo electrónico',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name, label in self.LABELS.items():
            self.fields[name].widget.attrs.setdefault('class', 'ctl')
            self.fields[name].label = label


# `Profile.USER_ROLES` labels are English ('professor', 'legal_tutor'), which
# is what `get_role_display()` renders app-wide. Changing them is a model
# concern with a migration attached; nothing here needs that, only to keep
# English out of a select on a Spanish page.
SPANISH_ROLE_LABELS = {
    'professor': 'Profesor@',
    'student': 'Estudiante',
    'tutor': 'Tutor legal',
    'administrator': 'Administrador@',
}


class AccountCreationForm(forms.Form):
    """An administrator creating a login for a professor, student or tutor.

    **`administrator` is deliberately not among the choices**, and that is a
    security boundary rather than an oversight. `ProfileAdmin.get_readonly_fields`
    makes `role` writable only to a superuser (see the
    `profile-admin-privilege-escalation` finding), so an in-app form that could
    mint administrators would hand every administrator session the escalation
    that constraint exists to withhold. The second administrator is created by
    `manage.py create_administrator`, where the gate is shell access on the
    server rather than a cookie.

    One POST form with no cascade, for the same reason
    `create_and_assign_student` has none: a GET round trip to narrow the
    identity control by role would either discard the password already typed
    or put it in the query string. All three identity controls therefore
    render at once and `clean()` enforces the one the chosen role needs,
    ignoring the others. It works with JavaScript off because there is nothing
    to degrade.
    """

    # Read from the module-level dict rather than a sibling class attribute:
    # a comprehension body gets its own scope and **skips the enclosing class
    # namespace**, so `ROLE_LABELS.get(...)` inside one raises NameError at
    # import time. `ROLE_LABELS` below is the public spelling of the same
    # mapping, which `views.create_account_view` reads for its message.
    ROLE_LABELS = SPANISH_ROLE_LABELS

    # Profile.USER_ROLES minus 'administrator'. Derived from the model's own
    # list rather than retyped, so a role added there appears here — with its
    # English label if nobody translates it, which is visible rather than
    # silent. The one value that must *not* leak through is named explicitly,
    # and `AccountCreationTests` asserts on its absence from the markup.
    ASSIGNABLE_ROLES = [(value, SPANISH_ROLE_LABELS.get(value, label))
                        for value, label in Profile.USER_ROLES
                        if value != 'administrator']

    username = forms.CharField(
        max_length=150, label='Usuario',
        help_text='Con esto entrará en la aplicación.',
        widget=forms.TextInput(attrs={'autocomplete': 'off'}))
    password1 = forms.CharField(
        label='Contraseña', widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password'}))
    password2 = forms.CharField(
        label='Repite la contraseña', widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password'}))
    role = forms.ChoiceField(
        choices=ASSIGNABLE_ROLES, label='Rol',
        help_text='Los administradores no se crean aquí; se crean con '
                  '«manage.py create_administrator».')

    teacher = forms.ModelChoiceField(
        queryset=Teachers.objects.none(), required=False,
        label='Ficha de profesor@',
        help_text='Solo para el rol profesor. Sin ficha, la cuenta entra '
                  'pero no ve ninguna clase.')
    student = forms.ModelChoiceField(
        queryset=Students.objects.none(), required=False,
        label='Ficha de alumn@',
        help_text='Solo para el rol estudiante.')
    children = forms.ModelMultipleChoiceField(
        queryset=Students.objects.none(), required=False,
        label='Alumn@s a su cargo',
        help_text='Solo para el rol tutor legal. Mantén pulsada la tecla '
                  'Ctrl/Cmd para seleccionar varios.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # `Profile.teacher` and `Profile.student` are OneToOne, so a row
        # already linked to an account cannot be offered again — the form
        # would validate and the save would raise IntegrityError. Excluding
        # them here is what makes the select state the truth.
        self.fields['teacher'].queryset = Teachers.objects.filter(
            profile__isnull=True).order_by('Name')
        self.fields['student'].queryset = Students.objects.filter(
            profile__isnull=True).order_by('Name')
        # A child may have several tutors: `children` is M2M, not OneToOne,
        # so this one is *not* filtered.
        self.fields['children'].queryset = Students.objects.order_by('Name')

        for name, field in self.fields.items():
            css = 'ctl'
            field.widget.attrs.setdefault('class', css)

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Ya existe una cuenta con ese usuario.')
        return username

    def clean_role(self):
        role = self.cleaned_data['role']
        # Belt and braces against a hand-built POST: ChoiceField already
        # rejects a value outside `choices`, and this says why in Spanish
        # rather than with Django's generic "Escoja una opción válida".
        if role == 'administrator':
            raise forms.ValidationError(
                'Los administradores no se crean desde la aplicación.')
        return role

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')

        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', 'Las dos contraseñas no coinciden.')
        elif password1:
            try:
                # The `user` argument is not optional in practice. Without it
                # UserAttributeSimilarityValidator returns immediately
                # (`if not user: return`), so the validator configured in
                # settings silently accepts password == username -- the single
                # highest-yield guess against a school where usernames follow a
                # scheme. There is no instance to pass here, so build the
                # unsaved User the validator needs to compare against.
                validate_password(
                    password1, User(username=cleaned.get('username') or ''))
            except DjangoValidationError as error:
                self.add_error('password1', list(error.messages))

        # The identity link the chosen role needs. A student account with no
        # `student` FK is a state the app already has a name for: `loginPage`
        # renders forbidden.html for it, so creating one here would be
        # manufacturing an account that cannot log in.
        if role == 'student' and not cleaned.get('student'):
            self.add_error(
                'student', 'Un estudiante necesita una ficha de alumn@.')
        if role == 'tutor' and not cleaned.get('children'):
            self.add_error(
                'children', 'Un tutor necesita al menos un alumn@ a su cargo.')
        # `teacher` is deliberately optional: `teacher_required` renders
        # forbidden.html with `unlinked_teacher`, which names the fix, so an
        # unlinked professor is a recoverable state rather than a broken one.

        return cleaned
