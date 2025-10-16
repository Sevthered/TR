from django import forms
from .models import Students, Profile, Course, Teachers, Subjects, Grade, Ausencias, Trimester


class CSVImportForm(forms.Form):
    csv_file = forms.FileField()


class GradeForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['student', 'subject', 'grade_type_number', 'grade_type',
                  'trimester', 'grade', 'comments']
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 3}),
        }
