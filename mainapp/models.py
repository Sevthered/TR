from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class School_year(models.Model):
    # Academic cycle (e.g., '2023-2024').
    SchoolYearID = models.AutoField(primary_key=True)
    year = models.CharField(
        max_length=9, help_text="Format: '2023-2024'")

    def __str__(self):
        return self.year


class Students(models.Model):
    # Basic student info.
    StudentID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)
    Email = models.EmailField(max_length=254)

    def __str__(self):
        return f"{self.Name}"


class Students_Courses(models.Model):
    # Links Student to a Course Section.
    student = models.ForeignKey(
        'Students', on_delete=models.CASCADE)
    course_section = models.ForeignKey(
        'Course',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Assigned Course Section"
    )

    class Meta:
        unique_together = ('student', 'course_section')

    def __str__(self):
        return f"{self.student.Name}"


class Profile(models.Model):
    # Extends User with roles.
    USER_ROLES = [
        ('professor', 'professor'),
        ('student', 'student'),
        ('tutor', 'legal_tutor'),
        ('administrator', 'administrator'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=USER_ROLES)
    student = models.OneToOneField(
        'Students',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    children = models.ManyToManyField(
        'Students',
        related_name='tutors',
        blank=True
    )

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Course(models.Model):
    # Specific class section (e.g., "Eso 1A").
    COURSE_TYPE_CHOICES = [
        ("Eso", "Eso"),
        ("Bachillerato", "Bachillerato"),
        ("IB", "IB"),
    ]
    CourseID = models.AutoField(primary_key=True)
    Tipo = models.CharField(max_length=20, choices=COURSE_TYPE_CHOICES)
    Section = models.CharField(max_length=2)
    school_year = models.ForeignKey(School_year, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.Tipo} {self.Section}"


class Teachers(models.Model):
    # Teacher catalog.
    TeacherID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)

    def __str__(self):
        return self.Name


class Subjects_Courses(models.Model):
    # Assigns Subject + Teacher to a Course + Trimester.
    subject = models.ForeignKey('Subjects', on_delete=models.CASCADE)
    teacher = models.ForeignKey('Teachers', on_delete=models.CASCADE)
    course = models.ForeignKey('Course', on_delete=models.CASCADE)
    trimester = models.ForeignKey('Trimester', on_delete=models.CASCADE)
    # Subset of students taking this subject.
    assigned_course_sections = models.ManyToManyField('Students_Courses')

    def __str__(self):
        return f"{self.subject} - {self.teacher} ({self.course})"


class Subjects(models.Model):
    # Subject catalog.
    SubjectID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)

    def __str__(self):
        return self.Name


class Trimester(models.Model):
    # Trimesters within a School Year.
    NAME_CHOICES = [
        (1, "First"),
        (2, "Second"),
        (3, "Third"),
    ]
    TrimesterID = models.AutoField(primary_key=True)
    Name = models.IntegerField(choices=NAME_CHOICES)
    school_year = models.ForeignKey(School_year, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.Name} ({self.school_year})"


class Grade(models.Model):
    # Stores grades.
    GRADE_TYPE_CHOICES = [
        ("examen", "examen"),
        ("parcial", "parcial"),
        ("trimestral", "trimestral"),
        ("final", "final"),
        ("otros", "otros")
    ]

    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    trimester = models.ForeignKey(Trimester, on_delete=models.CASCADE)
    school_year = models.ForeignKey(School_year, on_delete=models.CASCADE)
    grade = models.DecimalField(max_digits=4, decimal_places=2, validators=[
                                MinValueValidator(0), MaxValueValidator(10)])
    grade_type = models.CharField(
        max_length=15, blank=False, choices=GRADE_TYPE_CHOICES)
    grade_type_number = models.PositiveIntegerField(blank=True, default=0)
    comments = models.TextField(blank=True)

    class Meta:
        unique_together = ('student', 'subject', 'trimester', 'school_year',
                           'grade_type', 'grade_type_number')

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.grade_type})"


class Ausencias(models.Model):
    # Records absences and delays.
    AUSENCIAS_TYPE_CHOICES = [
        ("Ausencia", "Ausencia"),
        ("Retraso", "Retraso"),
    ]
    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    trimester = models.ForeignKey(
        Trimester, on_delete=models.CASCADE)
    school_year = models.ForeignKey(School_year, on_delete=models.CASCADE)
    Tipo = models.CharField(max_length=20, choices=AUSENCIAS_TYPE_CHOICES)
    date_time = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ('student', 'subject', 'trimester', 'date_time')

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.date_time})"
