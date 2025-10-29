from django import forms
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester, Subjects_Courses, School_year


class CSVImportForm(forms.Form):
    # Formulario simple, no basado en modelos, diseñado únicamente para manejar la subida de un archivo.
    csv_file = forms.FileField()


class GradeForm(forms.ModelForm):
    # Formulario basado en el modelo Grade para crear o editar calificaciones.
    class Meta:
        model = Grade
        # Define los campos del modelo Grade que se expondrán al usuario.
        fields = ['subject',
                  'trimester', 'school_year', 'grade_type', 'grade_type_number', 'grade', 'comments']
        widgets = {
            # Personaliza el widget de comentarios como un área de texto de 3 filas.
            'comments': forms.Textarea(attrs={'rows': 3}),
        }


class AusenciaEditForm(forms.ModelForm):
    # Formulario basado en el modelo Ausencias, optimizado para la edición de un registro existente.
    class Meta:
        model = Ausencias
        fields = ['subject', 'trimester', 'school_year', 'Tipo', 'date_time']
        widgets = {
            # Usa un widget de selección de fecha/hora local para una interfaz de usuario más amigable.
            'date_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class AusenciaForm(forms.ModelForm):
    # Formulario avanzado para registrar una incidencia (Ausencia/Retraso) para MÚLTIPLES estudiantes.

    # Campo extra (no existe en el modelo Ausencias) para seleccionar varios estudiantes a la vez.
    students = forms.ModelMultipleChoiceField(
        # Inicialmente vacío, se rellena en __init__.
        queryset=Students.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': 6}),
        required=True,
        label='Estudiantes'
    )
    # Campo extra para capturar la fecha y hora de la incidencia.
    date_time = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        label='Fecha y hora'
    )

    class Meta:
        model = Ausencias
        # Se excluye el campo 'student' (clave foránea) porque se maneja el campo 'students' (plural)
        # para aplicar la misma incidencia a varios estudiantes en la vista.
        fields = ['subject', 'trimester', 'school_year', 'Tipo']

    def __init__(self, *args, course=None, **kwargs):
        # El constructor se modifica para permitir filtrar los choices del formulario por curso.
        super().__init__(*args, **kwargs)

        # Lógica para manejar si el parámetro 'course' es un objeto o solo un ID.
        if course is not None:
            if not hasattr(course, 'CourseID'):
                try:
                    # Intenta obtener el objeto Course si solo se pasó el ID.
                    course = Course.objects.get(CourseID=course)
                except Exception:
                    course = None

        if course is not None:
            # Filtra el QuerySet de estudiantes para mostrar solo los que pertenecen a este curso.
            self.fields['students'].queryset = Students.objects.filter(
                subjects_courses__course=course).distinct().order_by('Name')
            # Filtra las asignaturas para mostrar solo las que se imparten en este curso.
            self.fields['subject'].queryset = Subjects.objects.filter(
                subjects_courses__course=course).distinct()

        # Establece la hora actual como valor inicial por defecto para el campo date_time si no se proporciona.
        if 'initial' not in kwargs or 'date_time' not in kwargs.get('initial', {}):
            from django.utils import timezone
            now = timezone.localtime(timezone.now())
            # Formatea la hora en el formato YYYY-MM-DDTHH:MM, requerido por el widget 'datetime-local'.
            self.fields['date_time'].initial = now.strftime('%Y-%m-%dT%H:%M')


MAIN_COURSES = {
    # Diccionario de utilidad para el flujo de creación de cursos.
    'Eso': [1, 2, 3, 4],
    'Bachillerato': [1, 2],
    'IB': [1, 2],
}

# 1. Formulario para crear School_year


class SchoolYearForm(forms.ModelForm):
    # Formulario para crear un nuevo registro de School_year.
    class Meta:
        model = School_year
        fields = ['year']
        labels = {
            'year': 'Definir Año Escolar (Ej: 2025-2026)',
        }
        widgets = {
            # Placeholder para guiar al administrador sobre el formato.
            'year': forms.TextInput(attrs={'placeholder': 'Ej: 2025-2026'}),
        }

# 2. Formulario Base para Secciones Dinámicas (Paso 2)


class CourseSectionForm(forms.Form):
    # Formulario base utilizado en el FORMSET para la creación de secciones (Paso 2).

    # Campo oculto para llevar el nombre principal del curso (ej: '1' para 1º ESO).
    main_course_name = forms.CharField(widget=forms.HiddenInput())

    # Campo de solo lectura para mostrar el nombre del nivel al usuario (ej: '1º ESO').
    display_name = forms.CharField(
        label="",
        required=False,
        widget=forms.TextInput(attrs={'readonly': 'readonly'})
    )

    # Campo clave para definir la cantidad de secciones (letras) a crear.
    num_subsections = forms.IntegerField(
        label="Nº de Secciones (A, B, C...)",
        min_value=1,
        max_value=26,
        initial=3,
        help_text="Ej: 3 creará 1A, 1B, 1C."
    )

# 3. Formulario Principal (Paso 1)


class CourseCreationForm(forms.Form):
    # Formulario principal para iniciar el proceso de creación de cursos (Paso 1).

    course_tipo = forms.ChoiceField(
        # Usa las opciones definidas en el modelo Course.
        choices=Course.COURSE_TYPE_CHOICES,
        label="Tipo de Curso a Configurar"
    )

    school_year = forms.ModelChoiceField(
        # Muestra los años escolares más recientes primero.
        queryset=School_year.objects.all().order_by('-year'),
        label="Año Escolar",
        required=False
    )

    def __init__(self, *args, **kwargs):
        # Parámetros personalizados extraídos de kwargs.
        initial_school_year_id = kwargs.pop('initial_school_year_id', None)
        self.course_type_initial = kwargs.pop('course_type_initial', None)

        super().__init__(*args, **kwargs)

        # Si se proporciona un ID de año escolar inicial (desde la vista anterior), lo establece y lo deshabilita.
        if initial_school_year_id:
            self.fields['school_year'].initial = initial_school_year_id
            self.fields['school_year'].widget.attrs['disabled'] = True

        # Si se proporciona un tipo de curso inicial (al volver del Paso 2), lo establece y lo deshabilita.
        if self.course_type_initial:
            self.fields['course_tipo'].initial = self.course_type_initial
            self.fields['course_tipo'].widget.attrs['disabled'] = True

    def clean(self):
        # Método para manejar la validación y limpieza de datos, crucial para campos deshabilitados (que no vienen en self.cleaned_data).
        cleaned_data = super().clean()

        # Si el campo school_year fue deshabilitado, lo recuperamos manualmente.
        if 'school_year' not in cleaned_data:
            school_year_value = self.fields['school_year'].initial or self.data.get(
                'school_year')

            if school_year_value:
                try:
                    # Intenta obtener el objeto School_year para añadirlo a cleaned_data.
                    cleaned_data['school_year'] = School_year.objects.get(
                        pk=school_year_value)
                except School_year.DoesNotExist:
                    raise forms.ValidationError(
                        "El año escolar seleccionado no es válido.")

        return cleaned_data

# 4. SOLUCIÓN AL ImportError: 'GradeForm'
# Este bloque parece ser una redefinición para asegurar que GradeForm esté disponible.
# Utiliza '__all__' para incluir todos los campos.


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = '__all__'


class SubjectAssignmentForm(forms.Form):
    # Formulario para seleccionar la asignatura y el profesor en la vista 'assign_subjects'.

    subject = forms.ModelChoiceField(
        # Muestra todas las asignaturas.
        queryset=Subjects.objects.all().order_by('Name'),
        label="Asignatura",
        empty_label="Seleccione Asignatura",
        required=True
    )

    teacher = forms.ModelChoiceField(
        # Muestra todos los profesores.
        queryset=Teachers.objects.all().order_by('Name'),
        label="Profesor/a",
        empty_label="Seleccione Profesor/a",
        required=True
    )


class StudentCreationForm(forms.ModelForm):
    # Formulario para crear una nueva instancia del modelo Students.
    class Meta:
        model = Students
        fields = ['Name', 'Email']
        widgets = {
            # Widgets con placeholder para mejorar la guía de entrada de datos.
            'Name': forms.TextInput(attrs={'placeholder': 'Nombre Completo del Estudiante'}),
            'Email': forms.EmailInput(attrs={'placeholder': 'Correo Electrónico'}),
        }
