from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

# Create your models here.


class Students(models.Model):
    StudentID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)
    First_Surname = models.CharField(max_length=50)
    Last_Surname = models.CharField(max_length=50)
    Email = models.EmailField(max_length=254)

    def __str__(self):
        return f"{self.StudentID} {self.Email}"


class Course(models.Model):
    COURSE_TYPE_CHOICES = [
        ("Eso", "Eso"),
        ("Bachillerato", "Bachillerato"),
        ("IB", "IB"),
    ]
    CourseID = models.AutoField(primary_key=True)
    Tipo = models.CharField(max_length=20, choices=COURSE_TYPE_CHOICES)
    Section = models.CharField(max_length=2)


class Teachers(models.Model):
    TeacherID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)


class Subjects(models.Model):
    SubjectID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)


class Trimester(models.Model):
    Trimestre = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(3)])
