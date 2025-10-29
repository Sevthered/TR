from django import forms
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester


class CSVImportForm(forms.Form):
    csv_file = forms.FileField()


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['subject',
                  'trimester', 'grade_type', 'grade_type_number', 'grade', 'comments']
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 3}),
        }


class AusenciaEditForm(forms.ModelForm):
    class Meta:
        model = Ausencias
        fields = ['subject', 'trimester', 'Tipo', 'date_time']
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
        fields = ['subject', 'trimester', 'Tipo']

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
