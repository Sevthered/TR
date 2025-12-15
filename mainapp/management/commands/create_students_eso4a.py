from django.core.management.base import BaseCommand
from mainapp.models import Students, Course, School_year, Students_Courses
from faker import Faker
import unicodedata

class Command(BaseCommand):
    help = 'Generates 30 random students for Eso 4A in a specific School Year'

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=str,
            default='2026-2027',
            help='School year to generate students for (e.g. "2026-2027"). Default: "2026-2027"'
        )

    def handle(self, *args, **options):
        year_input = options['year']
        fake = Faker('es_ES')
        
        try:
            year_obj = School_year.objects.get(year=year_input)
        except School_year.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"School Year '{year_input}' not found."))
            return

        try:
            course_obj = Course.objects.get(
                Tipo='Eso', 
                Section='4A', 
                school_year=year_obj
            )
        except Course.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Course 'Eso 4A' for year '{year_input}' not found."))
            return

        # Cleanup existing students in this course
        self.stdout.write(f"Cleaning up existing students in {course_obj}...")
        existing_relations = Students_Courses.objects.filter(course_section=course_obj)
        student_ids = existing_relations.values_list('student_id', flat=True)
        count_deleted, _ = Students.objects.filter(StudentID__in=student_ids).delete()
        self.stdout.write(f"Deleted {count_deleted} existing students.")

        self.stdout.write(f"Generating 30 students for {course_obj}...")

        created_count = 0
        for _ in range(30):
            first_name = fake.first_name()
            last_name = fake.last_name()
            full_name = f"{first_name} {last_name}"
            
            # Email generation: first letter of name + first surname
            # Normalize to remove accents: 'García' -> 'Garcia'
            normalized_name = self.normalize_text(first_name)
            normalized_surname = self.normalize_text(last_name)
            
            if normalized_name and normalized_surname:
                email_prefix = normalized_name[0] + normalized_surname
                email = f"{email_prefix.lower()}@eisbarcelona.com"
            else:
                email = f"student{created_count}@eisbarcelona.com"

            student = Students.objects.create(
                Name=full_name,
                Email=email
            )

            Students_Courses.objects.create(
                student=student,
                course_section=course_obj
            )
            
            created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Successfully generated {created_count} students for {course_obj}."))

    def normalize_text(self, text):
        return ''.join(c for c in unicodedata.normalize('NFD', text)
                       if unicodedata.category(c) != 'Mn')
