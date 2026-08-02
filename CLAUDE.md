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

# Tests — 223 tests in mainapp/tests.py
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

The admin course/subject-assignment forms are dependent dropdowns driven by jQuery hitting dedicated JSON endpoints in `views.py`: `ajax_get_course_numbers`, `ajax_get_course_sections`, `ajax_get_students`, `ajax_get_destination_courses`, `load_course_sections`. Adding a new dependent select means adding an endpoint here plus a `path('ajax/...')` entry. `load_trimesters` used to belong to this list; it is now htmx and returns markup — see the v2 section below.

`MAIN_COURSES` in `mainapp/forms.py` defines valid level numbers per course type (`Eso: 1-4`, `Bachillerato: 1-2`, `IB: 1-2`) and drives the two-step course-creation flow (`create_courses_step1.html` → `step2`, rendered via the `_render_step2` helper).

### Templates

Two roots: project-level `templates/` (`base_v2.html`, `base_shell_v2.html`, `forbidden.html`) and app-level `mainapp/templates/` split into `mainapp/` (professor/student/tutor pages) and `adminage/` (administrator flows). Static CSS/JS in `static/css/`, `static/js/`.

**`base.html` is gone**, along with `navbar.html`, `sidebar.html`, `navbar.css`, `sidebar.css`, `site-pages.css` and `behaviors.js` — every `mainapp` page is on the v2 cascade. Do not reintroduce any of them; `LegacyCascadeTeardownTests` asserts they stay deleted and that nothing extends `base.html`.

The one survivor is **`static/css/global-styles.css`**, which four `adminage/` templates link directly. It is not dead code and the same test class pins that too — see *the administrator flows* below.

| | v2 |
|---|---|
| Base | `templates/base_v2.html` — bare document; `templates/base_shell_v2.html` adds the teacher's nav + top-bar shell |
| CSS | Tailwind v4, source `static/css/src/app.css` → built `static/css/tailwind.css` |
| Pages | everything under `mainapp/templates/mainapp/` and `templates/mainapp/`, plus `templates/forbidden.html` |

Most pages extend `base_shell_v2.html` and fill `{% block main %}` / `{% block breadcrumb %}` / `{% block page_title %}`. **Two extend `base_v2.html` directly, deliberately:** `login.html` (a signed-out visitor has no nav) and `student_file.html` (every destination in that shell is `@teacher_required`, and the page is served only to students and tutors, so the whole chrome would answer 403). If a second student/tutor page appears, extract a role-aware shell rather than copying `student_file`'s inline one.

Copy the shipped pages rather than inventing a second dialect: 1px rules only (no shadows, no lighter card surfaces), no icon tiles, `lbl` for labels, `fig` reserved for numerals and identifiers, `ctl` on form controls, and the `ruled` filler where a list ends short. A student's initials come from `student_initials()` in `views.py`, so every list marks a person the same way.

**`_student_record.html` is a shell-free partial, and two pages include it.** It holds the filter bar and the grades and absences tables, and carries no `{% extends %}`, no blocks and no `<h1>` — each including page supplies its own heading. `student_dashboard_content.html` includes it for a professor; `student_file.html` includes it for a student, and for a tutor with `with student=… grades=… ausencias=…` overriding the three so the selected child's record renders. Its filter links must stay path-relative (`href="?school_year_id=…"`), never `{% url %}`-built, because it renders under two different routes. The contract is written at the top of the file.

> This split exists because `student_file.html` used to `{% include %}` `student_dashboard_content.html` — **including a template that `{% extends %}` another renders the entire extended document inline.** The page emitted its own markup before any doctype and then a complete second `<html>` document nested inside a `<div>`. Quirks mode, empty `<title>`. If you ever include a page template, that is what you get.

> **Migrating a page is not a re-skin — check its JavaScript first.** `base_v2` loads htmx and nothing else, so `static/js/behaviors.js` is absent and every `data-action` / `data-autosubmit` attribute the CSP remediation introduced is **inert** on a v2 page, silently. Both `teacher_dashboard` and `section_courses` had a `<select data-autosubmit>` year filter, and `section_courses` a `data-action="back"` button; the filters became rows of links, which is also what the class dashboard's scope bar does, and the back button gave way to the shell's breadcrumb. `grade_form`'s jQuery trimester cascade became htmx, and its `data-action="back"` cancel button a real link. Assume any legacy control that submits itself needs rebuilding, not copying. `V2CascadeAssertions.assert_no_inert_js_hooks` pins it, and `assert_no_leaked_template_comments` pins a mistake the rebuild actually made: **`{# … #}` is single-line only** — spread over two lines Django renders it as visible text, and it still looks like a comment in the editor. Use `{% comment %}` for anything multi-line.

**The write forms are rendered field by field, never `form.as_p`.** `as_p` emits `<p>` wrappers this cascade has no styles for. `grade_form.html`, `ausencia_form.html` and the absence panel in `_class_scope.html` share one dialect: a `lbl` label, the widget, then errors as `text-bad`. Spanish labels and the `ctl` class are attached in `forms.py`, not the template, because Django renders the widget itself — `GradeForm.LABELS` and `AusenciaEditForm.LABELS`.

**`ajax_load_trimesters` returns markup, not JSON.** Same route and same `@role_required('professor')`; it now renders `mainapp/_trimester_options.html`, which htmx swaps into `#id_trimester` when the year select changes (`hx-get`/`hx-target` live on the widget in `GradeForm.__init__`). It accepts the year as `school_year` — the select's own name, which is what htmx sends — or `school_year_id`. Option text must stay in step with `GradeForm.label_from_instance`; the same list is also rendered server-side on first paint, because `GradeForm.__init__` now honours `initial['school_year']` so the form is usable with JavaScript off.

**`class_dashboard` renders a fragment, not always a page.** `_class_scope.html` is everything below the page title — metrics strip, scope bar, register, absence panel — and the view returns *only* that file when the request is a **GET carrying `HX-Request`**. The scope-bar links are real `<a href>` with `hx-boost` layered on top, so the page still works with JavaScript off; the boost is scoped to that bar deliberately, because boosting the operations bar would AJAX the CSV downloads. Anything scope-dependent that lives *outside* the fragment has to be swapped out-of-band — today that is the nav's enrolled count (`id="class-enrolled"`), emitted only on an HTMX request so a full page load has no duplicate id.

### The administrator flows — still legacy, and never in the page count

Eight templates (`mainapp/templates/adminage/*.html` plus `mainapp/templates/reassign_students.html`, ~1,850 lines) are untouched by the overhaul. **None of them extends anything**, which is exactly why no `{% extends %}` sweep ever listed them: each is its own `<!DOCTYPE>` with its own inline `<style>`. Tailwind's Preflight would collide with those blocks, so they are not on the v2 cascade and must not be half-migrated.

- Four link `static/css/global-styles.css` — the reason that sheet outlived `base.html`.
- These are the jQuery AJAX-cascade pages (`ajax_get_course_numbers`, `ajax_get_course_sections`, `ajax_get_students`, `ajax_get_destination_courses`, `load_course_sections`). `grade_form` is the worked example of converting one: the endpoint returns markup, htmx swaps it, the attributes go on the widget in `forms.py`.
- `modify_assignments.html` has **no doctype at all** — the same defect `student_file.html` had, found independently.
- `create_and_assign_student.html` is the app's only light-themed page.
- `adminage_dashboard.html` still carries `/* ... (TUS ESTILOS CSS COMPLETOS AQUÍ) ... */` where its stylesheet was meant to go.

Inline `<style>` is not a CSP problem — `style-src` allows `'unsafe-inline'` deliberately and `settings.py` says why. It is a consistency problem. See the *Stage 3* section of `wiki/decisions/ui-overhaul.md`.

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
