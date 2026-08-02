from django import forms
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

    subject = forms.ModelChoiceField(
        queryset=Subjects.objects.all().order_by('Name'),
        label="Subject",
        empty_label="Select Subject",
        required=True
    )

    teacher = forms.ModelChoiceField(
        queryset=Teachers.objects.all().order_by('Name'),
        label="Professor",
        empty_label="Select Professor",
        required=True
    )


class StudentCreationForm(forms.ModelForm):
    # Form for creating a new Student.
    class Meta:
        model = Students
        fields = ['Name', 'Email']
        widgets = {
            'Name': forms.TextInput(attrs={'placeholder': 'Full Student Name'}),
            'Email': forms.EmailInput(attrs={'placeholder': 'Email Address'}),
        }
