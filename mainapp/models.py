from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class Students(models.Model):
    StudentID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)
    First_Surname = models.CharField(max_length=50)
    Last_Surname = models.CharField(max_length=50)
    Email = models.EmailField(max_length=254)

    def __str__(self):
        return f"{self.Name} {self.First_Surname} {self.Last_Surname}"


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
    school_year = models.CharField(max_length=9)  # e.g., "2023-2024"

    def __str__(self):
        return self.Name


class Grade(models.Model):
    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teachers, on_delete=models.SET_NULL, null=True)
    trimester = models.ForeignKey(
        Trimester, on_delete=models.SET_NULL, null=True)
    grade = models.DecimalField(max_digits=4, decimal_places=2, validators=[
                                MinValueValidator(0), MaxValueValidator(10)])
    comments = models.TextField(blank=True)

    def __str__(self):
        return f"{self.student} - {self.subject} - {self.grade}"


class Ausencias(models.Model):

    AUSENCIAS_TYPE_CHOICES = [
        ("Ausencia", "Ausencia"),
        ("Retraso", "Retraso"),
    ]

    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    trimester = models.ForeignKey(
        Trimester, on_delete=models.SET_NULL, null=True)
    teacher = models.ForeignKey(Teachers, on_delete=models.SET_NULL, null=True)
    Tipo = models.CharField(max_length=20, choices=AUSENCIAS_TYPE_CHOICES)
    date_time = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.Tipo} - {self.date_time.strftime('%Y-%m-%d %H:%M')} - {self.student.Name} {self.student.First_Surname} {self.student.Last_Surname}"
