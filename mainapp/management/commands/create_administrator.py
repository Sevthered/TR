"""Create an administrator account: a `User` plus a `Profile` with role
`administrator`, in one transaction.

**This is deliberately not a page in the application**, and the reasoning is
the security boundary rather than convenience. `ProfileAdmin.get_readonly_fields`
makes `Profile.role` writable only by a superuser, so an in-app form that could
mint administrators would hand every administrator session precisely the
escalation that constraint exists to withhold — and a stolen administrator
cookie would become a permanent foothold rather than a session. Here the gate
is shell access on the server, which is the same gate `createsuperuser` uses
and one an attacker with a browser does not have.

It is also not `createsuperuser`. An administrator `Profile` is **not**
`is_staff`: Django admin is a separate door and is correctly closed to it. This
command creates the application role and nothing else, so the person it creates
can run the school but cannot edit the database through `/admin/`.
"""

import getpass

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mainapp.models import Profile


class Command(BaseCommand):
    help = ("Crea una cuenta de administrador (User + Profile con rol "
            "'administrator'). No crea un superusuario de Django.")

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument(
            '--password',
            help=('Contraseña. Si se omite, se pide por consola sin eco — '
                  'que es lo preferible, porque un argumento queda en el '
                  'historial del shell y en la lista de procesos.'))

    def handle(self, *args, **options):
        username = options['username']

        # Checked before the password is asked for: making someone type a
        # password twice only to be told the username was taken is the kind
        # of small rudeness a command-line tool has no excuse for.
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"Ya existe una cuenta «{username}».")

        password = options['password']
        if not password:
            password = getpass.getpass('Contraseña: ')
            if password != getpass.getpass('Repite la contraseña: '):
                raise CommandError('Las dos contraseñas no coinciden.')

        if not password:
            raise CommandError('La contraseña no puede estar vacía.')

        # The same validators the in-app form runs. A command that skipped
        # them would make the shell the way to get a weak password in.
        try:
            validate_password(password)
        except ValidationError as error:
            raise CommandError('\n'.join(error.messages))

        # One transaction: a `User` with no `Profile` gets HTTP 500 on a
        # correct password, which is the whole finding this command answers.
        # Creating one here would be answering it with itself.
        with transaction.atomic():
            user = User.objects.create_user(
                username=username, password=password)
            Profile.objects.create(user=user, role='administrator')

        self.stdout.write(self.style.SUCCESS(
            f"Administrador «{username}» creado."))
        self.stdout.write(
            "No es superusuario: no puede entrar en /admin/, y eso es "
            "deliberado.")
