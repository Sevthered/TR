from django import forms
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 1. Order school year (newest first)
        self.fields['school_year'].queryset = School_year.objects.all().order_by(
            '-year')

        # 2. Empty trimester queryset on creation
        if not self.instance or not self.instance.pk:
            self.fields['trimester'].queryset = Trimester.objects.none()
            self.fields['trimester'].empty_label = "Select a school year"
            self.fields['trimester'].widget.attrs['disabled'] = True

        # 3. Limit queryset on edit
        elif self.instance.school_year:
            self.fields['trimester'].queryset = Trimester.objects.filter(
                school_year=self.instance.school_year
            ).order_by('Name')


class AusenciaEditForm(forms.ModelForm):
    # Form for editing an existing absence.
    class Meta:
        model = Ausencias
        fields = ['subject', 'trimester', 'school_year', 'Tipo', 'date_time']
        widgets = {
            'date_time': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class AusenciaForm(forms.ModelForm):
    # Advanced form for registering absences for multiple students.

    # Extra field for selecting multiple students.
    students = forms.ModelMultipleChoiceField(
        queryset=Students.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': 6}),
        required=True,
        label='Students'
    )
    # Extra field for date/time.
    date_time = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        label='Date and Time'
    )

    class Meta:
        model = Ausencias
        fields = ['subject', 'trimester', 'school_year', 'Tipo']

    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Handle course ID or object.
        if course is not None:
            if not hasattr(course, 'CourseID'):
                try:
                    course = Course.objects.get(CourseID=course)
                except Exception:
                    course = None

        if course is not None:
            # Filter students by course section.
            self.fields['students'].queryset = Students.objects.filter(
                students_courses__course_section=course).distinct().order_by('Name')

            # Filter subjects by course.
            self.fields['subject'].queryset = Subjects.objects.filter(
                subjects_courses__course=course).distinct()

            # Filter School Year to the one associated with the course
            self.fields['school_year'].queryset = School_year.objects.filter(
                pk=course.school_year.pk)
            self.fields['school_year'].initial = course.school_year

            # Filter Trimesters to those in the course's school year
            self.fields['trimester'].queryset = Trimester.objects.filter(
                school_year=course.school_year).order_by('Name')

        # Set default date/time to now.
        if 'initial' not in kwargs or 'date_time' not in kwargs.get('initial', {}):
            from django.utils import timezone
            now = timezone.localtime(timezone.now())
            self.fields['date_time'].initial = now.strftime('%Y-%m-%dT%H:%M')


MAIN_COURSES = {
    # Helper dictionary for course creation flow.
    'Eso': [1, 2, 3, 4],
    'Bachillerato': [1, 2],
    'IB': [1, 2],
}


class SchoolYearForm(forms.ModelForm):
    # Form for creating a new School Year.
    class Meta:
        model = School_year
        fields = ['year']
        labels = {
            'year': 'Define School Year (e.g., 2025-2026)',
        }
        widgets = {
            'year': forms.TextInput(attrs={'placeholder': 'e.g., 2025-2026'}),
        }


class CourseSectionForm(forms.Form):
    # Base form for dynamic sections (Step 2).

    main_course_name = forms.CharField(widget=forms.HiddenInput())

    display_name = forms.CharField(
        label="",
        required=False,
        widget=forms.TextInput(attrs={'readonly': 'readonly'})
    )

    num_subsections = forms.IntegerField(
        label="No. of Sections (A, B, C...)",
        min_value=1,
        max_value=26,
        initial=3,
        help_text="e.g., 3 will create 1A, 1B, 1C."
    )


class CourseCreationForm(forms.Form):
    # Main form for course creation (Step 1).

    course_tipo = forms.ChoiceField(
        choices=Course.COURSE_TYPE_CHOICES,
        label="Course Type"
    )

    school_year = forms.ModelChoiceField(
        queryset=School_year.objects.all().order_by('-year'),
        label="School Year",
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
                        "Invalid school year.")

        return cleaned_data


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = '__all__'


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
