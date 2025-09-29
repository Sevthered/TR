from django.db import models
from django.contrib.auth.models import User


class Students(models.Model):
    StudentID = models.AutoField(primary_key=True)
    Name = models.CharField(max_length=50)
    First_Surname = models.CharField(max_length=50)
    Last_Surname = models.CharField(max_length=50)
    Email = models.EmailField(max_length=254)
    # Optionally, add a user link if you want direct access from Students to User
    # user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.Name} {self.First_Surname} {self.Last_Surname}"


class Profile(models.Model):
    USER_ROLES = [
        ('professor', 'Professor'),
        ('student', 'Student'),
        ('tutor', 'Legal Tutor'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=USER_ROLES)
    # For students: link to their Students record
    student = models.OneToOneField(
        'Students', null=True, blank=True, on_delete=models.SET_NULL)
    # For tutors: link to all their children (students)
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

    def __str__(self):
        return self.Name

# Example: You can add a Grades model to link students, subjects, teachers, and trimesters


class Grade(models.Model):
    student = models.ForeignKey(Students, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subjects, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teachers, on_delete=models.SET_NULL, null=True)
    trimester = models.ForeignKey(
        Trimester, on_delete=models.SET_NULL, null=True)
    grade = models.DecimalField(max_digits=4, decimal_places=2)
    comments = models.TextField(blank=True)

    def __str__(self):
        return f"{self.student} - {self.subject}: {self.grade}"
