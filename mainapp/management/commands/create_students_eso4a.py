from django.core.management.base import BaseCommand, CommandError
from mainapp.models import Students, Course, School_year, Students_Courses
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
        parser.add_argument(
            '--force',
            action='store_true',
            help='Unassign students already in the target course before seeding.'
        )

    def handle(self, *args, **options):
        year_input = options['year']
        # Imported here, not at module level: Faker is a dev-only dependency
        # (requirements-dev.txt), and a module-level import would make every
        # `manage.py` command fail on a runtime-only install.
        try:
            from faker import Faker
        except ImportError:
            raise CommandError(
                'Faker is not installed. This is a development-only seed '
                'command; install requirements-dev.txt to use it.')

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

        # Cleanup existing students in this course.
        #
        # This used to delete the Students rows themselves. Grade and Ausencias
        # both cascade from Students, so seeding a course wiped every grade and
        # absence those students had -- irreversibly, with no confirmation, and
        # with a default --year that points at a plausible live year.
        existing_relations = Students_Courses.objects.filter(
            course_section=course_obj)
        count = existing_relations.count()
        if count and not options['force']:
            self.stdout.write(self.style.ERROR(
                f"{course_obj} already has {count} student(s). "
                f"Re-run with --force to unassign them from this course. "
                f"The student records themselves, and their grades, are kept."))
            return

        self.stdout.write(f"Unassigning {count} student(s) from {course_obj}...")
        deleted, _ = existing_relations.delete()
        self.stdout.write(
            f"Removed {deleted} course assignment(s). Student records kept.")

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
