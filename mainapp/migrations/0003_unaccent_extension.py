from django.contrib.postgres.operations import UnaccentExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Install the `unaccent` extension that search_students already assumes.

    Without it the accent-insensitive query raises UndefinedFunction and the
    view falls back to loading every Students row and filtering in Python.
    That fallback only survives because Django runs in autocommit: inside any
    atomic block the failed query aborts the transaction and every subsequent
    query in the request fails.
    """

    dependencies = [
        ('mainapp', '0002_alter_school_year_year_and_more'),
    ]

    operations = [
        UnaccentExtension(),
    ]
