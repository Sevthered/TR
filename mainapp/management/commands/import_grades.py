import csv
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mainapp.models import (
    Grade, School_year, Students, Subjects, Trimester,
)


class Command(BaseCommand):
    """Import grades from a CSV.

    Rewritten: the previous version passed a string to the `school_year`
    ForeignKey and to the integer `Trimester.Name`, and wrote a `date_assigned`
    field that does not exist on Grade. Every row therefore raised, was
    swallowed by a bare `except`, and the command reported a clean summary
    while importing nothing.

    Column names match the template produced by `download_class_list`, so a
    downloaded template round-trips through this command.
    """

    help = 'Import grades from a CSV file produced by the class-list download.'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to CSV file')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Validate every row and report, without writing anything.')

    def handle(self, *args, **options):
        created = updated = errors = 0
        dry_run = options['dry_run']

        try:
            # utf-8-sig: Excel's "CSV UTF-8" writes a BOM, which under plain
            # utf-8 lands inside the first header name and makes the student
            # column unreadable. A BOM-less file decodes identically.
            handle = open(
                options['csv_file'], newline='', encoding='utf-8-sig')
        except OSError as exc:
            raise CommandError(f'Could not open the CSV: {exc}')

        with handle:
            for row_num, row in enumerate(csv.DictReader(handle), start=2):
                try:
                    with transaction.atomic():
                        was_created = self._import_row(row, dry_run)
                    created += was_created
                    updated += not was_created
                except (Students.DoesNotExist, Subjects.DoesNotExist,
                        School_year.DoesNotExist, Trimester.DoesNotExist,
                        ValidationError, ValueError, TypeError, KeyError,
                        # Decimal's ValueError, and not a subclass of it.
                        InvalidOperation) as exc:
                    # Row numbers only: names and emails in a log line are a
                    # liability, and this stream is usually aggregated.
                    self.stderr.write(
                        f'Row {row_num}: {type(exc).__name__}')
                    errors += 1

        self.stdout.write(self.style.SUCCESS('Import summary'))
        self.stdout.write(f'  created: {created}')
        self.stdout.write(f'  updated: {updated}')
        self.stdout.write(f'  errors : {errors}')
        if dry_run:
            self.stdout.write(self.style.WARNING(
                '  dry run: nothing was written'))
        if errors:
            raise CommandError(f'{errors} row(s) failed.')

    def _import_row(self, row, dry_run):
        """Import one row. Returns True if a Grade was created."""
        grade_raw = _cell(row, 'Nota', 'grade')
        if not grade_raw:
            # A blank cell is a missing grade, not a zero.
            raise ValueError('missing grade')

        student = Students.objects.get(
            Name=_cell(row, 'Nombre_Estudiante', 'student_name'))
        subject = Subjects.objects.get(
            Name=_cell(row, 'Asignatura', 'subject_name'))
        # Looked up, never created: an import must not invent school years.
        school_year = School_year.objects.get(
            year=_cell(row, 'Año_Escolar', 'school_year'))
        trimester = Trimester.objects.get(
            Name=int(_cell(row, 'Trimestre', 'trimester_name')),
            school_year=school_year)

        key = dict(
            student=student,
            subject=subject,
            trimester=trimester,
            school_year=school_year,
            grade_type=_cell(row, 'Tipo_Nota', 'grade_type') or 'examen',
            grade_type_number=int(
                _cell(row, 'Numero_Tipo_Nota', 'grade_type_number') or 0),
        )
        grade = Grade.objects.filter(**key).first()
        created = grade is None
        if created:
            grade = Grade(**key)
        # Decimal, not float: `Grade.grade` is DecimalField(decimal_places=2)
        # and Django converts an assigned float through
        # `create_decimal_from_float()`, so float('7.7') reaches the validator
        # with three decimal places and is rejected. Only binary-exact values
        # — the multiples of 0.5 — ever imported. Same fix as the view.
        grade.grade = Decimal(grade_raw.replace(',', '.'))
        comments = _cell(row, 'Comentarios', 'comments')
        # Blank means "not stated", not "erase it": the `download_class_list`
        # template always ships `Comentarios` empty, so re-uploading it wiped
        # the comment on every grade it touched.
        if comments or created:
            grade.comments = comments
        # update_or_create skips validators, which is how out-of-range grades
        # and invalid grade types were getting in.
        grade.full_clean()
        if not dry_run:
            grade.save()
        return created


def _cell(row, *names):
    """First non-empty value among `names`, stripped."""
    for name in names:
        value = row.get(name)
        if value:
            return value.strip()
    return ''
