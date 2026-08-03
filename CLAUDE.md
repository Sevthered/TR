# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Django 5.2 school management app (Spanish-language UI, Spanish/English mixed code comments). Manages school years, course sections, subject/teacher assignments, grades, and absences for four user roles.

## Commands

```bash
source venv/bin/activate          # all commands below assume active venv

podman-compose up -d              # start PostgreSQL (compose.yaml, port 5432)
python manage.py migrate
python manage.py createcachetable # REQUIRED — see the rate-limiting section below
python manage.py runserver        # http://127.0.0.1:8000/

python manage.py makemigrations mainapp
python manage.py createsuperuser
python manage.py shell

# Seed data
python manage.py create_students_eso4a --year "2026-2027"   # 30 Faker students into Eso 4A
python manage.py import_grades path/to/file.csv             # bulk grade import (CLI variant)

# Tests — 375 tests in mainapp/tests.py
# POSTGRES_TEST_DB overrides the test database name, so a second worktree
# can run the suite against the same Postgres without colliding.
python manage.py test mainapp
python manage.py test mainapp.tests.SomeTest.test_method     # single test

./count.sh                        # scc line count, excludes venv/pycache
```

`./run.sh` starts DB + server in a tmux session. `PROJECT_DIR` now derives from `BASH_SOURCE`, so it works from any checkout.

Secrets come from the environment: `SECRET_KEY` is required (`settings.py:32`, raises if unset), `DEBUG` defaults to `False`, `ALLOWED_HOSTS` is env-driven. See `.env.example`.

## Architecture

Single Django app `mainapp` + project config `tr_webpage`. All URLs live in `mainapp/urls.py` (included at project root). All 30+ views live in one file, `mainapp/views.py` (~2400 lines).

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

`Subjects_Courses.assigned_course_sections` is an M2M to `Students_Courses` (enrolments, not students) and is **the per-subject roster** — students in one course legitimately take different subjects. Caveat: only one administrator form writes it, and that form `.clear()`s it on an empty submit (`views.py:1974-1976`), so it is often empty. It is unsafe for *authorization*; that is not the same as being meaningless. **Its one reader is `resolve_class_scope()`**, which uses it only to *narrow* a roster, only when non-empty, and only after re-filtering it by `course_section=course` — nothing constrains an enrolment in that M2M to belong to the course that owns the row. Everything downstream consumes the *resolved* roster (`scope.students`) rather than the M2M: the register, `class_metrics()`, and `AusenciaForm`, which is built from the scope so the panel cannot offer a student the register has just excluded.

`Grade` has **no FK to `Course`, `Teachers` or `Subjects_Courses`**. Joining grades to a class goes through `Students_Courses` and is unenforced.

Note the legacy naming: model fields are CapitalCase (`Name`, `Tipo`, `Section`, `Email`), models mix singular/plural (`Students`, `Grade`, `Ausencias`), and PKs are explicit AutoFields (`StudentID`, `CourseID`, …). Match the surrounding style rather than normalizing.

### School-year scoping

Nearly every list/dashboard view is filtered by school year, passed as the `?school_year_id=` query param (sometimes `?school_year=`), and often `?trimester_id=` too. When adding a view or a redirect, propagate these params — `create_school_year_view` for example redirects with `?school_year_id={pk}` appended. When no year is given, code falls back to `School_year.objects.all().order_by('-year').first()`.

**`class_dashboard` is the exception, deliberately.** It resolves scope through `resolve_class_scope()` → `ClassScope` (`views.py`, beside `teacher_courses`), which takes `?trimester_id=` and `?subject_courses_id=` and **ignores `?school_year_id=`** — `Course.school_year` fixes the year, and a year disagreeing with the course would describe a class that does not exist. Unrecognised ids fall back to the default scope rather than raising. Use `scope.query_string` to keep redirects and links inside the current scope. **Do not append `?school_year_id=` to a `class_dashboard` link** — it reads as a filter that does not exist. `section_courses.html` used to; it no longer does, and `teacher_dashboard.html` never did.

### Dependent dropdowns

**No JSON endpoint is left in the app.** Every cascade is htmx swapping server-rendered markup, and there are exactly two: `load_course_sections` (the administrator's course cascade — type → nivel → sección) and `ajax_load_trimesters` (the professor's year → trimestre). Both are described below. Adding a new dependent select means a view that renders a partial plus a `path('ajax/...')` entry — never a `JsonResponse`; `JsonResponse` is no longer imported by `views.py` at all.

The five that used to be here are gone. `load_trimesters` became markup with `grade_form`; `ajax_get_course_numbers`, `ajax_get_course_sections`, `ajax_get_students` and `ajax_get_destination_courses` were `reassign_students.html`'s private API, addressed by hardcoded URL strings, and were deleted with it — `ajax_get_students` in particular answered a roster of names and e-mail addresses.

`MAIN_COURSES` in `mainapp/forms.py` defines valid level numbers per course type (`Eso: 1-4`, `Bachillerato: 1-2`, `IB: 1-2`) and drives the two-step course-creation flow (`create_courses_step1.html` → `step2`, rendered via the `_render_step2` helper).

### Templates

Two roots: project-level `templates/` (`base_v2.html`, `base_shell_v2.html`, `forbidden.html`) and app-level `mainapp/templates/` split into `mainapp/` (professor/student/tutor pages) and `adminage/` (administrator flows). Static CSS/JS in `static/css/`, `static/js/`.

**`base.html` is gone**, along with `navbar.html`, `sidebar.html`, `navbar.css`, `sidebar.css`, `site-pages.css`, `global-styles.css` and `behaviors.js` — **every page in the app is on the v2 cascade**, `adminage/` included. Do not reintroduce any of them; `LegacyCascadeTeardownTests` asserts they stay deleted, that nothing extends `base.html`, and that Tailwind is the only stylesheet on disk.

| | v2 |
|---|---|
| Base | `templates/base_v2.html` — bare document; `templates/base_shell_v2.html` adds the teacher's nav + top-bar shell |
| CSS | Tailwind v4, source `static/css/src/app.css` → built `static/css/tailwind.css` |
| Pages | everything under `mainapp/templates/mainapp/` and `templates/mainapp/`, plus `templates/forbidden.html` |

Most pages extend `base_shell_v2.html` and fill `{% block main %}` / `{% block breadcrumb %}` / `{% block page_title %}`. `login.html` extends `base_v2.html` directly, because a signed-out visitor has no nav.

**`base_shell_v2`'s nav is role-aware, and that is load-bearing.** It branches on `user.profile.role` into professor / student+tutor+legal_tutor / administrator, because every destination in the professor's nav is `@teacher_required` — rendering it to anyone else is offering a menu of 403s. Each role's entries stay written out as literals rather than looped over a context variable: the shell is extended by pages that know nothing about the nav, and a missing variable would render an empty one silently. Active state comes from `request.resolver_match`, so a page gets it by existing at that route.

`forbidden.html` extends the shell too, and a denied account keeps **its own** nav there — the 403 is where someone most needs a way back. A role with no branch gets the mark, the identity footer and no links.

**Every template in the app now extends `base_v2` or `base_shell_v2`** — `LegacyCascadeTeardownTests.test_every_template_is_on_the_v2_cascade` states that invariant directly, exempting only `base_v2.html` itself and the four `_`-prefixed partials. It asserts on `{% extends %}` and deliberately *not* on the string `<!DOCTYPE`: several migrated pages name the doctype they dropped in a `{% comment %}`, which is the same way the old `global-styles.css` guard came to pass against a comment.

Copy the shipped pages rather than inventing a second dialect: 1px rules only (no shadows, no lighter card surfaces), no icon tiles, `lbl` for labels, `fig` reserved for numerals and identifiers, `ctl` on form controls, and the `ruled` filler where a list ends short. A student's initials come from `student_initials()` in `views.py`, so every list marks a person the same way.

**`_student_record.html` is a shell-free partial, and two pages include it.** It holds the filter bar and the grades and absences tables, and carries no `{% extends %}`, no blocks and no `<h1>` — each including page supplies its own heading. `student_dashboard_content.html` includes it for a professor; `student_file.html` includes it for a student, and for a tutor with `with student=… grades=… ausencias=…` overriding the three so the selected child's record renders. Its filter links must stay path-relative (`href="?school_year_id=…"`), never `{% url %}`-built, because it renders under two different routes. The contract is written at the top of the file.

> This split exists because `student_file.html` used to `{% include %}` `student_dashboard_content.html` — **including a template that `{% extends %}` another renders the entire extended document inline.** The page emitted its own markup before any doctype and then a complete second `<html>` document nested inside a `<div>`. Quirks mode, empty `<title>`. If you ever include a page template, that is what you get.

> **Migrating a page is not a re-skin — check its JavaScript first.** `base_v2` loads htmx and nothing else, so `static/js/behaviors.js` is absent and every `data-action` / `data-autosubmit` attribute the CSP remediation introduced is **inert** on a v2 page, silently. Both `teacher_dashboard` and `section_courses` had a `<select data-autosubmit>` year filter, and `section_courses` a `data-action="back"` button; the filters became rows of links, which is also what the class dashboard's scope bar does, and the back button gave way to the shell's breadcrumb. `grade_form`'s jQuery trimester cascade became htmx, and its `data-action="back"` cancel button a real link. Assume any legacy control that submits itself needs rebuilding, not copying. `V2CascadeAssertions.assert_no_inert_js_hooks` pins it, and `assert_no_leaked_template_comments` pins a mistake the rebuild actually made: **`{# … #}` is single-line only** — spread over two lines Django renders it as visible text, and it still looks like a comment in the editor. Use `{% comment %}` for anything multi-line.

**The write forms are rendered field by field, never `form.as_p`.** `as_p` emits `<p>` wrappers this cascade has no styles for. `grade_form.html`, `ausencia_form.html` and the absence panel in `_class_scope.html` share one dialect: a `lbl` label, the widget, then errors as `text-bad`. Spanish labels and the `ctl` class are attached in `forms.py`, not the template, because Django renders the widget itself — `GradeForm.LABELS` and `AusenciaEditForm.LABELS`.

**`ajax_load_trimesters` returns markup, not JSON.** Same route and same `@role_required('professor')`; it now renders `mainapp/_trimester_options.html`, which htmx swaps into `#id_trimester` when the year select changes (`hx-get`/`hx-target` live on the widget in `GradeForm.__init__`). It accepts the year as `school_year` — the select's own name, which is what htmx sends — or `school_year_id`. Option text must stay in step with `GradeForm.label_from_instance`; the same list is also rendered server-side on first paint, because `GradeForm.__init__` now honours `initial['school_year']` so the form is usable with JavaScript off.

**`class_dashboard` renders a fragment, not always a page.** `_class_scope.html` is everything below the page title — metrics strip, scope bar, register, absence panel — and the view returns *only* that file when the request is a **GET carrying `HX-Request`**. The scope-bar links are real `<a href>` with `hx-boost` layered on top, so the page still works with JavaScript off; the boost is scoped to that bar deliberately, because boosting the operations bar would AJAX the CSV downloads. Anything scope-dependent that lives *outside* the fragment has to be swapped out-of-band — today that is the nav's enrolled count (`id="class-enrolled"`), emitted only on an HTMX request so a full page load has no duplicate id.

### The administrator flows — stage 3, complete

The `adminage/` templates were never in the overhaul's page count, because **none of them extended anything**: each was its own `<!DOCTYPE>` with its own inline `<style>`, so no `{% extends %}` sweep ever listed them. All seven are migrated, `reassign_students` last, and it moved into `adminage/` with them.

**`static/css/global-styles.css` is gone**, deleted with its last consumer. So is `simple-layout.css`, which nothing ever linked. **Tailwind is the only stylesheet on disk**, and `LegacyCascadeTeardownTests` pins that.

**`reassign_students` was the only rebuild in the whole overhaul, not a repair.** Every other legacy page was already broken by the CSP remediation — a `data-action` with no `behaviors.js` to bind it. This one's inline `<script>` carried a nonce, so its two cascades and its four private JSON endpoints all worked; there was a live feature to regress. What replaced them:

* The **origin** cascade is the shared one — `load_course_sections` and `adminage/_course_dependents.html`, inside a GET form with a submit button, exactly as `assign_subjects` does it. The old page had a second dialect for the same idea: year → type → *number* → *letter*, reassembled into a `Section` string by walking characters.
* The **destination** cascade was repeated per student, built by JS into each card with ids like `dest-course-type-{id}`, plus a fourth endpoint whose only job was resolving a `course_id` into a hidden input. It is now one grouped `<select name="assignments">` per row whose option value **is** the `student_id:course_id` pair the POST branch reads — nothing to keep in sync, nothing to resolve over the network. Same reasoning as `create_and_assign_student`: on a single POST form a GET round trip per row would discard every other row's choice.
* **The POST contract is unchanged** on purpose, so `ReassignStudentsTests` still describes the page. The empty option is `value=""`, which the view already skipped, so an untouched row submits nothing rather than a no-op counted as a success.
* Destinations span **every school year**, because promoting a student is the case that made the wrong-year enrolment bug matter. `_destination_groups()` orders in the database rather than pulling every course into Python to sort.
* The POST redirect carries the scope back (`_reassign_url`). It used to drop it, landing the administrator on an empty picker after every save.

**`load_course_sections` returns markup, not JSON**, and returns **the whole dependent block** (`adminage/_course_dependents.html`), not a bare `<option>` list. Changing the course type must repopulate *Nivel* and clear *Sección* — two targets, and htmx allows one `hx-target` per element. An out-of-band swap cannot do it: htmx wraps a fragment beginning with `<option>` in a `<select>` so the browser will parse it, so an oob `<select>` would end up nested inside one. `views.course_cascade_context()` feeds the same partial on first paint and on swap, so option text cannot drift between them. `hx-include` is scoped by id, not `closest form` — including the form sends a stale `level` and flips the endpoint into the wrong mode.

Arriving at `assign_subjects` or `reassign_students` with only `?course_id=` recovers the type and level **server-side** from the course row. That is what the never-implemented `LEVEL_LOOKUP` mode was for; the branch is dropped and its job is now the ordinary path. `reassign_students` also recovers the **type and level** from the row, overriding a disagreeing query string. **The year is the exception, and deliberately so since 2026-08-03**: when a `?school_year_id=` is submitted that disagrees with the course, the *course* is dropped and the year kept. With JavaScript off, picking a new year and pressing «Cargar alumnado» submits the previous year's `course_id` alongside it, and letting the row win silently reverted the year the administrator had just chosen. The view cannot distinguish a year typed by a person from one carried in a bookmark, so an explicit `school_year_id` is treated as the deliberate act. A year arriving with *no* `school_year_id` still comes from the row.

**A control that cannot do anything must say which of the two it is.** On a database holding one course, every destination select on `reassign_students` offered exactly one option — the class the student is already in — so the page looked operable and could move nobody. `no_other_destination` names that state and links to section creation. It was found by rendering the page against the live data, not by a test.

`create_and_assign_student` deliberately has **no cascade** — one `<select>` with an `<optgroup>` per course type. It is a single POST form, so a GET round trip would either discard the name and e-mail already typed or put a student's personal data in the query string.

**`modify_assignments.html` was deleted.** Unreachable from birth: added in `cec85d3` alongside the working `reassign_students.html`, rendered by no view, and its two AJAX endpoint names have never appeared in `urls.py` in any commit.

Inline `<style>` is not a CSP problem — `style-src` allows `'unsafe-inline'` deliberately and `settings.py` says why. It is a consistency problem. See the *Stage 3* section of `wiki/decisions/ui-overhaul.md`.

**Named groups (`group/act` + `group-hover/act:`) entered the dialect on `adminage_dashboard`.** A row there holds *two* independent links, and a plain row-wide `group` lights both on hovering either, reading as one target where there are two. Use a named group only for that case; a row with one link stays on plain `group`, as `section_courses` does.

**Django emits `aria-describedby="id_<field>_helptext"` on any widget whose field has help text.** If a rebuilt template renders `{{ field.help_text }}` without `id="{{ field.auto_id }}_helptext"` on the element, every such control points at nothing. It renders identically for a sighted reader, so only reading the markup catches it — `AdminFlowTemplateTests.test_the_help_text_is_actually_addressable` pins it.

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
| `grades_csv` (export) | `Estudiante, Asignatura, Trimestre, Año Escolar, Nota, Tipo de Nota, Numero tipo de Nota, Comentario` |
| `class_grades_download` (export) | as above **except** `Numero_Tipo_Nota` — the two exports disagree on that one column |

`download_class_list` is the only producer whose *headers* the importers accept; the two export sets must be re-headered before re-import. **Since 2026-08-03 the importer says so.** It validates `reader.fieldnames` up front and refuses the whole file naming the missing columns — previously an export fed back in cleared the header checks and then failed row by row with "Alumno no encontrado o fuera de tu clase", for students who plainly existed, because the name column had never been read.

**The round-trip works as of step 5.** It did not before: `download_class_list` derived `Año_Escolar` from `timezone.now()` rather than from `course.school_year`, so every row of the template failed on re-import with "año escolar no encontrado" whenever the calendar year and the school year disagreed. The template now writes `course.school_year.year`.

> [!danger] It only *half* worked until 2026-08-03, and the half that failed was invisible
> `import_grades` did `float(grade_raw.replace(',', '.'))` and handed a **float** to `Grade.grade`, a `DecimalField(max_digits=4, decimal_places=2)`. Django converts floats through `create_decimal_from_float()`, so `float('7.7')` becomes `Decimal('7.700')` — three decimal places — and `DecimalValidator` rejects it. **Only binary-exact values imported: 21 of 101 one-decimal grades, exactly the multiples of 0.5.** `2.66`, the only grade in the live database, was not among them, and neither was its own export.
>
> Two things kept it hidden. `CsvImportTests` used only `7.5`, `6` and `7,5` — all binary-exact. And the row-error handler rendered one catch-all string for four unrelated causes, **discarding Django's own** "Asegúrese de que no haya más de 2 dígitos decimales", which had been naming the bug all along.
>
> The fix is `Decimal(...)` in both importers, plus `InvalidOperation` in the except clauses — it inherits `ArithmeticError`, not `ValueError`, so the existing tuple did not cover it. **Any new test for a numeric import must use a value like `2.66` or `7.7`; a test written with `7.5` passes against the broken code.**

The fix is at the producer, deliberately. **`import_grades` still looks `School_year` and `Trimester` up and never creates them** — an upload must not be able to invent a school year, and `CsvImportTests` pins that. A missing year or trimester now names the offending value in the error. Import matches `Students` and `Subjects` **by exact name string**.

The exports are still unaffected by `LANGUAGE_CODE = 'es-es'`: they write `Decimal`s through `csv.writer`, which calls `str()`, so a grade stays `2.66` in the file while rendering `2,66` on a page.

## Known rough edges

The four items previously listed here (duplicate `GradeForm`, stray imports, unfinished filename block, hardcoded `DEBUG`/`SECRET_KEY`) were **all fixed** in the 2026-08-02 remediation. What remains:

- **`reassign_students` used to write to an arbitrary year's enrolment.** Fixed 2026-08-03, and worth knowing because the shape recurs: `Students_Courses` is unique on `(student, course_section)` and carries **no year of its own** — the year lives on `Course.school_year`. So a student holds one row per course, and any lookup of "this student's enrolment" that does not filter by year picks an arbitrary one. See `wiki/findings/reassign-writes-to-the-wrong-year.md`.
- **Aggregates exist in exactly one place: `class_metrics()`.** Everywhere else, any average, total or rate is still a **new backend feature**, not a display change. `class_metrics` is also the pattern to copy: two aggregate queries plus a Python merge, never one annotated queryset over `Students` — `grade` and `ausencias` are both multi-valued, so annotating both at once multiplies rows and each inflates the other's count. Class means there are **weighted by grade count**; a mean of means is a different number. `grade_count` has **no denominator** (see the `Grade` uniqueness note above).
- ~~`sort_key_section` raises on an odd `Section`~~ — **fixed 2026-08-03.** The key is total now (`re.match(r'(\d+)(\D*)')`, falling back to `(999, section)`), and reading the number greedily also fixed `'10'` sorting as `(1, '0')`. It mattered because nothing validates `Section` on the way in, so one bad administrator submit permanently broke `/teacher/` and `/section/<x>/courses/` **for every professor** — a different role, with no in-app way to see why. `CourseSectionForm` now validates against `MAIN_COURSES` as well, so both the cause and the symptom are closed.
- ~~The bulk-absence loop swallows every exception~~ — **fixed 2026-08-03.** It now separates `IntegrityError` (duplicate) from `ValidationError` and names the skipped students. The sharper bug underneath was that a *partial* failure was reported as unqualified success: two students selected with one colliding gave "Ausencias creadas para 1 estudiante(s)." and no hint the other was skipped. A residual `except Exception` remains deliberately — it logs through `audit()` rather than swallowing, because letting one odd row 500 would lose a 30-student batch's successful writes.
- **`LANGUAGE_CODE = 'es-es'`.** Decimals render `2,66` and dates take Spanish formats app-wide. Two consequences worth knowing before adding a widget: `<input type="datetime-local">` and `type="date"` need an explicit `format='%Y-%m-%dT%H:%M'` on the widget, because Django otherwise renders `DATETIME_INPUT_FORMATS[0]` of the locale and the browser **silently blanks the control**; and anything writing a number into a file rather than a page must keep going through `str()`, not the locale.

## Rate limiting needs a cache table the test suite creates for you

`CACHES` is `DatabaseCache` on table `django_cache` (`settings.py:250-255`), and that table is created by **`manage.py createcachetable`, not by a migration**. Every `@ratelimit` view — `grades_csv`, `search_students`, `import_grades`, `class_grades_download` — raises `ProgrammingError: relation "django_cache" does not exist` and returns 500 without it.

**The suite is structurally blind to this**, because Django's test runner calls `createcachetable` itself during `create_test_db`. A green run says nothing about whether the real database has the table. `.github/workflows/ci.yml` runs the command; the setup block above now does too. Verified present in the current dev database on 2026-08-03.

Rate-limit rejections come back as **429** via `RatelimitTo429Middleware`, deliberately distinct from the 403 a role check produces so logs can tell them apart. One caveat: django-axes 6.x also defaults to 429 for a lockout and `settings.py` does not override `AXES_HTTP_RESPONSE_CODE`, so the two are currently indistinguishable in logs after all.

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
