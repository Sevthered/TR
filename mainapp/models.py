from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class Students(models.Model):
    StudentID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)
    Email = models.EmailField(max_length=254)

    def __str__(self):
        return f"{self.Name} {self.Email}"


class Profile(models.Model):
    USER_ROLES = [
        ('professor', 'professor'),
        ('student', 'student'),
        ('tutor', 'legal_tutor'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=USER_ROLES)
    student = models.OneToOneField(
        'Students', null=True, blank=True, on_delete=models.SET_NULL)
    children = models.ManyToManyField(
        'Students', related_name='tutors', blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Course(models.Model):
    COURSE_TYPE_CHOICES = [
        ("Eso", "Eso"),
        ("Bachillerato", "Bachillerato"),
        ("IB", "IB"),
    ]
    CourseID = models.AutoField(primary_key=True)
    Tipo = models.CharField(max_length=20, choices=COURSE_TYPE_CHOICES)
    Section = models.CharField(max_length=2)

    def __str__(self):
        return f"{self.Tipo} {self.Section}"


class Teachers(models.Model):
    TeacherID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)

    def __str__(self):
        return self.Name


class Subjects(models.Model):
    SubjectID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)

    def __str__(self):
        return self.Name


class Trimester(models.Model):
    TrimesterID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=20)
    school_year = models.CharField(
        max_length=9, help_text="Año escolar: '2023-2024'")

    def __str__(self):
        return f"{self.Name} ({self.school_year})"


class Grade(models.Model):
    GRADE_TYPE_CHOICES = [
        ("examen", "examen"),
        ("parcial", "parcial"),
        ("trimestral", "trimestral"),
        ("final", "final"),
        ("otros", "otros")
    ]

    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teachers, on_delete=models.SET_NULL, null=True)
    trimester = models.ForeignKey(Trimester, on_delete=models.CASCADE)
    grade = models.DecimalField(max_digits=4, decimal_places=2, validators=[
                                MinValueValidator(0), MaxValueValidator(10)])
    grade_type = models.CharField(
        max_length=15, blank=False, choices=GRADE_TYPE_CHOICES)
    grade_type_number = models.PositiveIntegerField(blank=True, default=0)
    comments = models.TextField(blank=True)

    class Meta:
        unique_together = ('student', 'subject', 'trimester',
                           'grade_type', 'grade_type_number')

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.grade_type})"


class Ausencias(models.Model):
    AUSENCIAS_TYPE_CHOICES = [
        ("Ausencia", "Ausencia"),
        ("Retraso", "Retraso"),
    ]
    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    trimester = models.ForeignKey(
        Trimester, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teachers, on_delete=models.SET_NULL, null=True)
    Tipo = models.CharField(max_length=20, choices=AUSENCIAS_TYPE_CHOICES)
    date_time = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('student', 'subject', 'trimester', 'date_time')

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.date_time})"
