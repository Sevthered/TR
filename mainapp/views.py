from django.shortcuts import render
from .models import Students
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
# Create your views here.


def index(request):
    students = Students.objects.all()
    context = {"students": students}
    return render(request, "mainapp/index.html", context)


def student_detail(request, student_name):
    student = get_object_or_404(Students, StudentID=student_name)
    return render(request, "mainapp/students.html", {"student": student})
