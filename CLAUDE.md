# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Django 5.2 school management app (Spanish-language UI, Spanish/English mixed code comments). Manages school years, course sections, subject/teacher assignments, grades, and absences for four user roles.

## Commands

```bash
source venv/bin/activate          # all commands below assume active venv

podman-compose up -d              # start PostgreSQL (compose.yaml, port 5432)
python manage.py migrate
python manage.py runserver        # http://127.0.0.1:8000/

python manage.py makemigrations mainapp
python manage.py createsuperuser
python manage.py shell

# Seed data
python manage.py create_students_eso4a --year "2026-2027"   # 30 Faker students into Eso 4A
python manage.py import_grades path/to/file.csv             # bulk grade import (CLI variant)

# Tests — 128 tests in mainapp/tests.py
# POSTGRES_TEST_DB overrides the test database name, so a second worktree
# can run the suite against the same Postgres without colliding.
python manage.py test mainapp
python manage.py test mainapp.tests.SomeTest.test_method     # single test

./count.sh                        # scc line count, excludes venv/pycache
```

`./run.sh` starts DB + server in a tmux session. `PROJECT_DIR` now derives from `BASH_SOURCE`, so it works from any checkout.

Secrets come from the environment: `SECRET_KEY` is required (`settings.py:32`, raises if unset), `DEBUG` defaults to `False`, `ALLOWED_HOSTS` is env-driven. See `.env.example`.

## Architecture

Single Django app `mainapp` + project config `tr_webpage`. All URLs live in `mainapp/urls.py` (included at project root). All 30+ views live in one file, `mainapp/views.py` (~2280 lines).

### Role model

`Profile` (`mainapp/models.py`) one-to-ones Django's `User` and adds `role` ∈ `professor | student | tutor | administrator`, plus `student` (FK for student accounts) and `children` (M2M for tutor accounts).

**Authorization IS decorator-based** (changed during the 2026-08-02 security remediation). Use the decorators, not inline role checks:

```python
@role_required('professor')          # views.py:74-93 — 403 + forbidden.html on mismatch
@teacher_required                    # views.py:96-112 — the above PLUS profile.teacher is not None
```

Object scoping goes through `teacher_courses(profile.teacher)` (`views.py:115-117`) and `teacher_students(profile.teacher)` (`views.py:120-130`). New professor views must use both — a decorator alone does not stop a teacher reading another teacher's students. `loginPage` routes by role: student/tutor → `student_dashboard`, professor → `teacher_dashboard`, administrator → `adminage_dashboard`.

### Data model chain

```
School_year ──< Trimester
     └──< Course (Tipo ∈ Eso|Bachillerato|IB, Section e.g. "1A")
              └──< Students_Courses (Students ↔ Course, unique_together)
                        └──< Subjects_Courses.assigned_course_sections (M2M)

Subjects_Courses = Subject + Teacher + Course + Trimester   # the teaching assignment
Grade / Ausencias = Student + Subject + Trimester + School_year (+ grade_type/date_time)
```

`Grade` is unique on `(student, subject, trimester, school_year, grade_type, grade_type_number)`. **A student can hold unbounded grades per subject per trimester** — 5 `grade_type` values x unbounded `grade_type_number`. Any UI showing a grade count therefore has **no denominator**. `grade_type_number` defaults to `0`, and 0 means *unnumbered*, not "the first one" (`student_dashboard_content.html:152` renders it as "unico"). `Ausencias` is unique on `(student, subject, trimester, date_time)`; note `Tipo` is NOT in that key.

`Subjects_Courses.assigned_course_sections` is an M2M to `Students_Courses` (enrolments, not students) and is **the per-subject roster** — students in one course legitimately take different subjects. Caveat: only one administrator form writes it, and that form `.clear()`s it on an empty submit (`views.py:1885-1890`), so it is often empty. It is unsafe for *authorization*; that is not the same as being meaningless. **Its one reader is `resolve_class_scope()`**, which uses it only to *narrow* a roster, only when non-empty, and only after re-filtering it by `course_section=course` — nothing constrains an enrolment in that M2M to belong to the course that owns the row. Everything downstream consumes the *resolved* roster (`scope.students`) rather than the M2M: the register, `class_metrics()`, and `AusenciaForm`, which is built from the scope so the panel cannot offer a student the register has just excluded.

`Grade` has **no FK to `Course`, `Teachers` or `Subjects_Courses`**. Joining grades to a class goes through `Students_Courses` and is unenforced.

Note the legacy naming: model fields are CapitalCase (`Name`, `Tipo`, `Section`, `Email`), models mix singular/plural (`Students`, `Grade`, `Ausencias`), and PKs are explicit AutoFields (`StudentID`, `CourseID`, …). Match the surrounding style rather than normalizing.

### School-year scoping

Nearly every list/dashboard view is filtered by school year, passed as the `?school_year_id=` query param (sometimes `?school_year=`), and often `?trimester_id=` too. When adding a view or a redirect, propagate these params — `create_school_year_view` for example redirects with `?school_year_id={pk}` appended. When no year is given, code falls back to `School_year.objects.all().order_by('-year').first()`.

**`class_dashboard` is the exception, deliberately.** It resolves scope through `resolve_class_scope()` → `ClassScope` (`views.py`, beside `teacher_courses`), which takes `?trimester_id=` and `?subject_courses_id=` and **ignores `?school_year_id=`** — `Course.school_year` fixes the year, and a year disagreeing with the course would describe a class that does not exist. Unrecognised ids fall back to the default scope rather than raising. Use `scope.query_string` to keep redirects and links inside the current scope. **Do not append `?school_year_id=` to a `class_dashboard` link** — it reads as a filter that does not exist. `section_courses.html` used to; it no longer does, and `teacher_dashboard.html` never did.

### AJAX cascades

The admin course/subject-assignment forms are dependent dropdowns driven by jQuery hitting dedicated JSON endpoints in `views.py`: `ajax_get_course_numbers`, `ajax_get_course_sections`, `ajax_get_students`, `ajax_get_destination_courses`, `load_course_sections`, `load_trimesters`. Adding a new dependent select means adding an endpoint here plus a `path('ajax/...')` entry.

`MAIN_COURSES` in `mainapp/forms.py` defines valid level numbers per course type (`Eso: 1-4`, `Bachillerato: 1-2`, `IB: 1-2`) and drives the two-step course-creation flow (`create_courses_step1.html` → `step2`, rendered via the `_render_step2` helper).

### Templates

Two roots: project-level `templates/` (`base.html`, `navbar.html`, `sidebar.html`, `forbidden.html`) and app-level `mainapp/templates/` split into `mainapp/` (professor/student/tutor pages) and `adminage/` (administrator flows). `base.html` includes navbar + sidebar; static CSS/JS in `static/css/`, `static/js/`.

`templates/sidebar.html` is entirely hardcoded — no loop, no context variable, no template tag. (`mainapp/templatetags/sidebar_extras.py` and its partial were deleted; only an `__init__.py` remains.)

**Two cascades coexist, on purpose.** A UI overhaul is migrating pages one at a time:

| | Legacy | v2 |
|---|---|---|
| Base | `templates/base.html` | `templates/base_v2.html`, plus `templates/base_shell_v2.html` for the nav + top-bar shell |
| CSS | the four hand-written stylesheets in `static/css/` | Tailwind v4, source `static/css/src/app.css` → built `static/css/tailwind.css` |
| Pages | everything else | `class_dashboard.html` + `_class_scope.html`, `teacher_dashboard.html` |

> **Migrating a page is not a re-skin — check its JavaScript first.** `base_v2` loads htmx and nothing else, so `static/js/behaviors.js` is absent and every `data-action` / `data-autosubmit` attribute the CSP remediation introduced is **inert** on a v2 page, silently. `teacher_dashboard`'s year filter was a `<select data-autosubmit>`; it became a row of links, which is also what the class dashboard's scope bar does. Assume any legacy control that submits itself needs rebuilding, not copying.

**`class_dashboard` renders a fragment, not always a page.** `_class_scope.html` is everything below the page title — metrics strip, scope bar, register, absence panel — and the view returns *only* that file when the request is a **GET carrying `HX-Request`**. The scope-bar links are real `<a href>` with `hx-boost` layered on top, so the page still works with JavaScript off; the boost is scoped to that bar deliberately, because boosting the operations bar would AJAX the CSV downloads. Anything scope-dependent that lives *outside* the fragment has to be swapped out-of-band — today that is the nav's enrolled count (`id="class-enrolled"`), emitted only on an HTMX request so a full page load has no duplicate id.

Tailwind's Preflight collides with the legacy stylesheets, which is why the bases are kept apart. Delete `base.html` when the last page migrates.

```bash
npm run css          # rebuild static/css/tailwind.css — REQUIRED after editing any template
npm run css:watch
```

Tailwind v4 discovers sources relative to the CSS file, so `app.css` names both template roots with explicit `@source` lines. **A new template root needs a third line or its classes get tree-shaken out of the bundle.** Tokens (`--color-ground`, `--spacing-row`, …) live in the `@theme` block there; see `wiki/decisions/ui-overhaul.md`.

## CSV import/export

Three different header sets exist and they do **not** all match:

| Producer | Headers |
|---|---|
| `download_class_list` (import template) | `Nombre_Estudiante, Asignatura, Trimestre, Año_Escolar, Nota, Tipo_Nota, Numero_Tipo_Nota, Comentarios` |
| `import_grades` (accepted) | same as above, or English fallbacks (`student_name`, `subject_name`, …) |
| `grades_csv` / `class_grades_download` (exports) | `Estudiante, Asignatura, Trimestre, Año Escolar, Nota, Tipo de Nota, …, Comentario` |

`download_class_list` is the only producer whose *headers* the importers accept; the two export sets must be re-headered before re-import.

**The round-trip works as of step 5.** It did not before: `download_class_list` derived `Año_Escolar` from `timezone.now()` rather than from `course.school_year`, so every row of the template failed on re-import with "año escolar no encontrado" whenever the calendar year and the school year disagreed. The template now writes `course.school_year.year`.

The fix is at the producer, deliberately. **`import_grades` still looks `School_year` and `Trimester` up and never creates them** — an upload must not be able to invent a school year, and `CsvImportTests` pins that. A missing year or trimester now names the offending value in the error. Import matches `Students` and `Subjects` **by exact name string**.

The exports are still unaffected by `LANGUAGE_CODE = 'es-es'`: they write `Decimal`s through `csv.writer`, which calls `str()`, so a grade stays `2.66` in the file while rendering `2,66` on a page.

## Known rough edges

The four items previously listed here (duplicate `GradeForm`, stray imports, unfinished filename block, hardcoded `DEBUG`/`SECRET_KEY`) were **all fixed** in the 2026-08-02 remediation. What remains:

- **Aggregates exist in exactly one place: `class_metrics()`.** Everywhere else, any average, total or rate is still a **new backend feature**, not a display change. `class_metrics` is also the pattern to copy: two aggregate queries plus a Python merge, never one annotated queryset over `Students` — `grade` and `ausencias` are both multi-valued, so annotating both at once multiplies rows and each inflates the other's count. Class means there are **weighted by grade count**; a mean of means is a different number. `grade_count` has **no denominator** (see the `Grade` uniqueness note above).
- **`sort_key_section`** (`views.py:631-641`) raises on any `Section` not shaped `<digit><letter>`.
- **`views.py:811-815`** swallows every exception in the bulk-absence loop, so an `IntegrityError` and a `ValidationError` look identical to the teacher.
- **`LANGUAGE_CODE = 'es-es'`.** Decimals render `2,66` and dates take Spanish formats app-wide. Two consequences worth knowing before adding a widget: `<input type="datetime-local">` and `type="date"` need an explicit `format='%Y-%m-%dT%H:%M'` on the widget, because Django otherwise renders `DATETIME_INPUT_FORMATS[0]` of the locale and the browser **silently blanks the control**; and anything writing a number into a file rather than a page must keep going through `str()`, not the locale.

## Content-Security-Policy

`script-src 'self'` with **no `unsafe-inline`**, per-response nonces, no CDN allowance (`settings.py:82-93`). Consequences for any frontend work:

- All JS must be vendored under `static/js/vendor/` (jQuery already is). No CDN `<script>`.
- No webfont URLs and no Google Fonts `@import` — they are blocked and fall back **silently**.
- HTMX `hx-on:` attributes eval strings and violate the policy. Stay on `hx-get` / `hx-post` / `hx-boost`.
- A screenshot cannot reveal any of these. Check the browser console.

## Project wiki

An Obsidian vault lives in this repo at `wiki/`, with immutable sources under `.raw/`.

Read in this order — stop as soon as you have enough:

1. `wiki/hot.md` — recent context, ~500 words
2. `wiki/index.md` — full catalog
3. `wiki/overview.md` — executive summary of the codebase and its current state
4. Individual pages under `wiki/modules/`, `wiki/flows/`, `wiki/findings/`

Do **not** read the wiki for general Django or Python questions, or for anything already visible in the files you have open.

`wiki/findings/` holds 17 pages from a security and correctness review dated 2026-08-02. **All 17 are now fixed** — read them for the reasoning behind the current shape of the auth decorators, the CSP, the rate limits and the audit log, and to avoid undoing a deliberate decision.

A **full UI overhaul is in progress** — `wiki/decisions/ui-overhaul.md` is the live task, and `wiki/hot.md` carries the resume point. Read both before touching any template, `static/`, or `class_dashboard`.

Two conventions worth knowing before editing the vault:

- `[[Page-Name]]` citations inside `wiki/findings/` point at the **Tech-Books** vault (`/home/sebas/Obsidian/Tech-Books`), not this one. They render as unresolved links here on purpose. Do not create local stubs for them.
- This heading is `## Project wiki`, deliberately not `## Knowledge wiki` — the `tech-books-review` skill scans for the latter plus a `Vault:` path and would redirect future book reviews at this vault instead of Tech-Books.

Full conventions in `wiki/meta/conventions.md`. Operation history in `wiki/log.md` (append-only, newest first).
