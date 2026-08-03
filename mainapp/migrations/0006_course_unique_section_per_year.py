"""Make (Tipo, Section, school_year) unique on `Course`.

"Eso 1A of 2025-2026" names one class. The create-courses flow used
`bulk_create` against a model with no uniqueness of any kind, so running it
twice for the same type and level left two `IB 1A` rows in one year: identical
in every Sección select, different primary keys, and a roster split between
them — with `Students_Courses` unique on (student, course_section), a student
could legitimately hold a row in each.

Same rule as 0005 for pre-existing violations: refuse loudly and name them,
rather than let the database answer with a bare unique violation or let a
migration silently pick a survivor. Merging two sections means moving
enrolments and subject assignments, which is an administrative decision.
"""

from django.db import migrations, models


def refuse_duplicate_sections(apps, schema_editor):
    Course = apps.get_model('mainapp', 'Course')

    duplicates = (
        Course.objects
        .values('Tipo', 'Section', 'school_year__year')
        .annotate(rows=models.Count('pk'))
        .filter(rows__gt=1)
        .order_by('school_year__year', 'Tipo', 'Section')
    )

    if duplicates:
        listing = ', '.join(
            f"{row['Tipo']} {row['Section']} de {row['school_year__year']} "
            f"(x{row['rows']})" for row in duplicates)
        raise RuntimeError(
            "No se puede aplicar la restricción de unicidad: hay secciones "
            f"repetidas — {listing}. Fusiónelas o renómbrelas a mano antes de "
            "volver a ejecutar la migración; esta migración no borra ni "
            "fusiona filas, porque trasladar matrículas y asignaturas de una "
            "sección a otra es una decisión administrativa."
        )


class Migration(migrations.Migration):

    dependencies = [
        ('mainapp', '0005_school_year_unique'),
    ]

    operations = [
        migrations.RunPython(
            refuse_duplicate_sections, migrations.RunPython.noop,
            elidable=False),
        migrations.AlterUniqueTogether(
            name='course',
            unique_together={('Tipo', 'Section', 'school_year')},
        ),
    ]
