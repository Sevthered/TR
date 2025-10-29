import django
import os
import csv
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime
from mainapp.models import Grade, Students, Subjects, Teachers, Trimester, School_year
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tr_webpage.settings')
django.setup()


class Command(BaseCommand):
    help = 'Import grades from CSV file with trimester school year support'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to CSV file')

    def handle(self, *args, **options):
        csv_file_path = options['csv_file']

        created_count = 0
        updated_count = 0
        error_count = 0

        with open(csv_file_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)

            # Start at 2 for header
            for row_num, row in enumerate(reader, start=2):
                try:
                    # Parse CSV row
                    student_name = row['student_name'].strip()
                    subject_name = row['subject_name'].strip()
                    trimester_name = row['trimester_name'].strip()
                    school_year = row['school_year'].strip()
                    grade_value = float(row['grade'])
                    comments = row.get('comments', '').strip()

                    # Get or create objects
                    student = Students.objects.get(
                        Name=student_name)
                    subject = Subjects.objects.get(Name=subject_name)
                    trimester, created = Trimester.objects.get_or_create(
                        Name=trimester_name,
                        school_year=school_year,
                        defaults={'Name': trimester_name,
                                  'school_year': school_year}
                    )

                    # Create or update grade
                    grade, created = Grade.objects.update_or_create(
                        student=student,
                        subject=subject,
                        trimester=trimester,
                        defaults={
                            'grade': grade_value,
                            'comments': comments,
                            'date_assigned': timezone.now(),
                        }
                    )

                    if created:
                        created_count += 1
                        self.stdout.write(f"✓ Created: {grade}")
                    else:
                        updated_count += 1
                        self.stdout.write(f"↻ Updated: {grade}")

                except Students.DoesNotExist:
                    self.stderr.write(
                        f"❌ Row {row_num}: Student '{student_name}' not found")
                    error_count += 1
                except Subjects.DoesNotExist:
                    self.stderr.write(
                        f"❌ Row {row_num}: Subject '{subject_name}' not found")
                    error_count += 1
                except Exception as e:
                    self.stderr.write(f"❌ Row {row_num}: {e}")
                    error_count += 1

        # Summary
        self.stdout.write(self.style.SUCCESS(f"\n📊 Import Summary:"))
        self.stdout.write(f"✓ Created: {created_count}")
        self.stdout.write(f"↻ Updated: {updated_count}")
        self.stdout.write(f"❌ Errors: {error_count}")
        self.stdout.write(
            f"📚 Total processed: {created_count + updated_count + error_count}")
