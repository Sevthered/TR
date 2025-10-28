from django import forms
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester, Subjects_Courses, School_year


class CSVImportForm(forms.Form):
    csv_file = forms.FileField()


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['subject',
                  'trimester', 'school_year', 'grade_type', 'grade_type_number', 'grade', 'comments']
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 3}),
        }


class AusenciaEditForm(forms.ModelForm):
    class Meta:
        model = Ausencias
        fields = ['subject', 'trimester', 'school_year', 'Tipo', 'date_time']
        widgets = {
            'date_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class AusenciaForm(forms.ModelForm):
    # Multi-select field for students (use plural name to avoid conflict with FK)
    students = forms.ModelMultipleChoiceField(
        queryset=Students.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': 6}),
        required=True,
        label='Estudiantes'
    )
    # Allow specifying date and time for the absence; default to now
    date_time = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        label='Fecha y hora'
    )

    class Meta:
        model = Ausencias
        # exclude the FK 'student' from Meta fields; we handle multiple students
        fields = ['subject', 'trimester', 'school_year', 'Tipo']

    def __init__(self, *args, course=None, **kwargs):
        # Allow caller to pass a Course instance (or course id) to limit
        # the student and subject choices to that course's attendees/subjects.
        super().__init__(*args, **kwargs)
        if course is not None:
            # Accept either Course instance or id
            if not hasattr(course, 'CourseID'):
                try:
                    course = Course.objects.get(CourseID=course)
                except Exception:
                    course = None

        if course is not None:
            # students in this course via Subjects_Courses -> students M2M
            self.fields['students'].queryset = Students.objects.filter(
                subjects_courses__course=course).distinct().order_by('Name')
            # subjects taught in this course
            self.fields['subject'].queryset = Subjects.objects.filter(
                subjects_courses__course=course).distinct()
        # set default initial for date_time to now if not provided
        if 'initial' not in kwargs or 'date_time' not in kwargs.get('initial', {}):
            from django.utils import timezone
            now = timezone.localtime(timezone.now())
            # format for datetime-local: YYYY-MM-DDTHH:MM
            self.fields['date_time'].initial = now.strftime('%Y-%m-%dT%H:%M')


MAIN_COURSES = {
    'Eso': [1, 2, 3, 4],
    'Bachillerato': [1, 2],
    'IB': [1, 2],
}

# 1. Formulario para crear School_year


class SchoolYearForm(forms.ModelForm):
    class Meta:
        model = School_year
        fields = ['year']
        labels = {
            'year': 'Definir Año Escolar (Ej: 2025-2026)',
        }
        widgets = {
            'year': forms.TextInput(attrs={'placeholder': 'Ej: 2025-2026'}),
        }

# 2. Formulario Base para Secciones Dinámicas (Paso 2)


class CourseSectionForm(forms.Form):
    main_course_name = forms.CharField(widget=forms.HiddenInput())

    display_name = forms.CharField(
        label="",
        required=False,
        widget=forms.TextInput(attrs={'readonly': 'readonly'})
    )

    num_subsections = forms.IntegerField(
        label="Nº de Secciones (A, B, C...)",
        min_value=1,
        max_value=26,
        initial=3,
        help_text="Ej: 3 creará 1A, 1B, 1C."
    )

# 3. Formulario Principal (Paso 1)


class CourseCreationForm(forms.Form):
    course_tipo = forms.ChoiceField(
        choices=Course.COURSE_TYPE_CHOICES,  # Debe estar definido en models.py
        label="Tipo de Curso a Configurar"
    )

    school_year = forms.ModelChoiceField(
        queryset=School_year.objects.all().order_by('-year'),
        label="Año Escolar",
        required=False
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

        if 'school_year' not in cleaned_data:
            school_year_value = self.fields['school_year'].initial or self.data.get(
                'school_year')

            if school_year_value:
                try:
                    cleaned_data['school_year'] = School_year.objects.get(
                        pk=school_year_value)
                except School_year.DoesNotExist:
                    raise forms.ValidationError(
                        "El año escolar seleccionado no es válido.")

        return cleaned_data

# 4. SOLUCIÓN AL ImportError: 'GradeForm'


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = '__all__'


class SubjectAssignmentForm(forms.Form):
    subject = forms.ModelChoiceField(
        queryset=Subjects.objects.all().order_by('Name'),
        label="Asignatura",
        empty_label="Seleccione Asignatura",
        required=True
    )

    teacher = forms.ModelChoiceField(
        queryset=Teachers.objects.all().order_by('Name'),
        label="Profesor/a",
        empty_label="Seleccione Profesor/a",
        required=True
    )


class StudentCreationForm(forms.ModelForm):
    class Meta:
        model = Students
        fields = ['Name', 'Email']
        widgets = {
            'Name': forms.TextInput(attrs={'placeholder': 'Nombre Completo del Estudiante'}),
            'Email': forms.EmailInput(attrs={'placeholder': 'Correo Electrónico'}),
        }
