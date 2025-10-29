from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class School_year(models.Model):
    # Modelo para definir el ciclo académico (ej: '2023-2024'). Es la raíz temporal de la aplicación.
    SchoolYearID = models.AutoField(primary_key=True)
    year = models.CharField(
        max_length=9, help_text="Año escolar: '2023-2024'")

    def __str__(self):
        return self.year


class Students(models.Model):
    # Información básica del estudiante.
    StudentID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)
    Email = models.EmailField(max_length=254)

    def __str__(self):
        return f"{self.Name}"


class Students_Courses(models.Model):
    # Tabla de relación explícita (Many-to-Many) entre Estudiantes y la Sección de Curso.
    # Define a qué clase pertenece un estudiante en un año escolar específico.
    student = models.ForeignKey(
        # Si se borra el estudiante, se borra el registro de asignación.
        'Students', on_delete=models.CASCADE)
    course_section = models.ForeignKey(
        'Course',
        # Si el curso se elimina, el estudiante queda desasignado (NULL).
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Sección de Curso Asignada"
    )

    class Meta:
        # Asegura que un estudiante no esté dos veces en la misma sección de curso.
        unique_together = ('student', 'course_section')

    def __str__(self):
        return f"{self.student.Name}"


class Profile(models.Model):
    # Extensión del modelo User de Django para añadir roles y vínculos con Students.
    USER_ROLES = [
        ('professor', 'professor'),
        ('student', 'student'),
        ('tutor', 'legal_tutor'),
        ('administrator', 'administrator'),
    ]
    # Vínculo uno a uno con el usuario de Django.
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    # Rol del usuario en el sistema.
    role = models.CharField(max_length=20, choices=USER_ROLES)
    student = models.OneToOneField(
        'Students',
        null=True,
        blank=True,
        on_delete=models.SET_NULL  # Vínculo directo si el rol es 'student'.
    )
    children = models.ManyToManyField(
        'Students',
        related_name='tutors',
        # Vínculo M2M para el rol 'tutor', que puede tener varios hijos.
        blank=True
    )

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Course(models.Model):
    # Representa una sección de clase específica (ej: "Eso 1A").
    COURSE_TYPE_CHOICES = [
        ("Eso", "Eso"),
        ("Bachillerato", "Bachillerato"),
        ("IB", "IB"),
    ]
    CourseID = models.AutoField(primary_key=True)
    # Nivel educativo (Eso, Bachillerato).
    Tipo = models.CharField(max_length=20, choices=COURSE_TYPE_CHOICES)
    Section = models.CharField(max_length=2)  # Nivel y letra (ej: '1A', '2B').
    # Año escolar al que pertenece este curso.
    school_year = models.ForeignKey(School_year, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.Tipo} {self.Section}"


class Teachers(models.Model):
    # Catálogo de profesores.
    TeacherID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)

    def __str__(self):
        return self.Name


class Subjects_Courses(models.Model):
    # La tabla clave que define la asignación curricular: una asignatura, a un profesor, en un curso, durante un trimestre.
    subject = models.ForeignKey('Subjects', on_delete=models.CASCADE)
    teacher = models.ForeignKey('Teachers', on_delete=models.CASCADE)
    course = models.ForeignKey('Course', on_delete=models.CASCADE)
    trimester = models.ForeignKey('Trimester', on_delete=models.CASCADE)
    # Lista de Students_Courses (enlaces de estudiantes) a los que se aplica esta asignación.
    # Permite asignar una materia solo a un subconjunto de estudiantes dentro de un curso.
    assigned_course_sections = models.ManyToManyField('Students_Courses')

    def __str__(self):
        return f"{self.subject} - {self.teacher} ({self.course})"


class Subjects(models.Model):
    # Catálogo de asignaturas.
    SubjectID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)

    def __str__(self):
        return self.Name


class Trimester(models.Model):
    # Definición de los trimestres dentro de un año escolar (complementa School_year).
    NAME_CHOICES = [
        (1, "First"),
        (2, "Second"),
        (3, "Third"),
    ]
    TrimesterID = models.AutoField(primary_key=True)
    # El número del trimestre (1, 2, 3).
    Name = models.IntegerField(choices=NAME_CHOICES)
    # Vínculo al año escolar.
    school_year = models.ForeignKey(School_year, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.Name} ({self.school_year})"


class Grade(models.Model):
    # Modelo para almacenar calificaciones.
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
                                # La calificación en sí, restringida a [0, 10].
                                MinValueValidator(0), MaxValueValidator(10)])
    grade_type = models.CharField(
        # Tipo de evaluación.
        max_length=15, blank=False, choices=GRADE_TYPE_CHOICES)
    # Permite diferenciar varias notas del mismo tipo (Ej: Examen 1, Examen 2).
    grade_type_number = models.PositiveIntegerField(blank=True, default=0)
    comments = models.TextField(blank=True)

    class Meta:
        # Restricción crucial: No se permiten dos notas idénticas para el mismo contexto y número de tipo.
        unique_together = ('student', 'subject', 'trimester', 'school_year',
                           'grade_type', 'grade_type_number')

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.grade_type})"


class Ausencias(models.Model):
    # Modelo para registrar ausencias y retrasos.
    AUSENCIAS_TYPE_CHOICES = [
        ("Ausencia", "Ausencia"),
        ("Retraso", "Retraso"),
    ]
    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    trimester = models.ForeignKey(
        Trimester, on_delete=models.CASCADE)
    school_year = models.ForeignKey(School_year, on_delete=models.CASCADE)
    # Tipo de incidencia.
    Tipo = models.CharField(max_length=20, choices=AUSENCIAS_TYPE_CHOICES)
    # Fecha y hora exactas.
    date_time = models.DateTimeField(default=timezone.now)

    class Meta:
        # Asegura que no se registre dos veces la misma incidencia en el mismo momento.
        unique_together = ('student', 'subject', 'trimester', 'date_time')

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.date_time})"
