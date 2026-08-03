"""Make `School_year.year` unique.

Two rows named '2025-2026' are indistinguishable in every select in the app,
and the damage is not cosmetic: `School_year.objects.get(year=...)` is how the
CSV importer resolves its `Año_Escolar` column, so a duplicate turns every row
of every import into a swallowed `MultipleObjectsReturned` reported as a
generic failure. `order_by('-year').first()`, the app-wide default year, also
becomes an arbitrary pick between the two.

The guard below runs first and *refuses* rather than letting the database
report a bare unique violation, or worse letting anyone reach for a
deduplicating migration. Which of two identically named years a grade, a course
or a trimester belongs to is not recoverable from the rows, so nothing here may
choose; a human has to rename or merge them by hand and re-run.
"""

from django.db import migrations, models


def refuse_duplicate_years(apps, schema_editor):
    School_year = apps.get_model('mainapp', 'School_year')

    duplicates = (
        School_year.objects
        .values('year')
        .annotate(rows=models.Count('pk'))
        .filter(rows__gt=1)
        .order_by('year')
    )

    if duplicates:
        listing = ', '.join(
            f"{row['year']} (x{row['rows']})" for row in duplicates)
        raise RuntimeError(
            "No se puede aplicar la restricción de unicidad: hay años "
            f"escolares repetidos — {listing}. Renómbrelos o fusiónelos a "
            "mano antes de volver a ejecutar la migración; esta migración no "
            "borra ni fusiona filas, porque a cuál de los dos años pertenece "
            "cada nota, curso o trimestre no se puede deducir de los datos."
        )


class Migration(migrations.Migration):

    dependencies = [
        ('mainapp', '0004_profile_teacher'),
    ]

    operations = [
        migrations.RunPython(
            refuse_duplicate_years, migrations.RunPython.noop, elidable=False),
        migrations.AlterField(
            model_name='school_year',
            name='year',
            field=models.CharField(
                error_messages={
                    'unique': 'Ya existe un año escolar con ese nombre.'},
                help_text="Format: '2023-2024'", max_length=9, unique=True),
        ),
    ]
