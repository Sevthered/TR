# TR

## How this project differs from the shared standards (read first)

The shared engineering core loads from `.claude/rules/atlas` (→ `~/atlas/methodology-core/`) and
already carries: wiki-first · research→plan→execute · verify the real artifact · read the whole
error · the escalation tiers · don't-stop-early · vertical slices · fresh-context review before
merging · worktree isolation for parallel writers. **Those are not repeated here.** Memory files
concatenate, so the deltas below add to the core; they never relax it.

- ⛔ **This repository is PUBLIC** (`git@github.com:Sevthered/TR.git`). Never add a secret, a real
  credential, or student data to a tracked file. `settings.py` already contains a dev-only
  hardcoded `SECRET_KEY` and inline DB credentials in the public history — do not extend that
  pattern, and do not restate the values in docs.
- ⛔ **`main` is Sebas's hand-written project.** `ai-assisted-ui-overhaul` was cut from it and
  **never merges into it**. Ask before proposing any merge in that direction.
- **ADDS to `never` (say-so required):** `git commit` · `git push` · anything that writes to a
  database holding real student records.
- **ADDS to `ask me first`:** schema changes (there is only one migration — see Gotchas) ·
  destructive management commands · touching `Profile.role` or any authorization check.
- **WIKI FIRST names a specific location** — `wiki/` (see below) owns the security-finding record
  and the UI-overhaul design rules. Read `wiki/hot.md` before starting; never guess a fact the
  wiki already records.

## Project

**TR** is a Django 5.2 school-management web app for a Spanish school (levels ESO / Bachillerato /
IB). It covers the academic lifecycle — student enrollment, course/section structuring,
subject/teacher assignment, grading, and attendance ("Ausencias"). The UI mixes Spanish and
English; many model fields, URL names, and comments are in Spanish.

## Commands

Requires a running PostgreSQL and an activated `venv`. DB config is hardcoded in
`tr_webpage/settings.py` (db `my_database`, `127.0.0.1:5432`).

```bash
# Start Postgres (Podman/Docker) — compose service is named `db`, container `postgresql`
podman-compose up          # or: docker compose up

# One-shot dev launcher: activates venv, starts Postgres + runserver in a tmux session `dev-session`
./run.sh

# Manual run
source venv/bin/activate
python manage.py runserver
python manage.py migrate
python manage.py createsuperuser

# LOC count (needs `scc`)
./count.sh
```

**Tests:** none on `main` — `mainapp/tests.py` is the empty Django stub, no pytest/tox config.
The suite (437 tests, green) lives on `ai-assisted-ui-overhaul`, which does not merge to `main`.
Runner is `python manage.py test` (or `python manage.py test mainapp.tests.TestClass.test_method`).

### Management commands (`mainapp/management/commands/`)
- `python manage.py create_students_eso4a` — generates 30 random students for Eso 4A in a given
  school year. Test-data only; it writes real rows.
- `python manage.py import_grades` — CSV grade import.

## Architecture

Single Django app **`mainapp`** holds all logic; `tr_webpage/` is project config.
`mainapp/views.py` is a 1862-line module — the whole request layer lives there.

### Domain model (`mainapp/models.py`)
The schema is year-scoped and section-based. Read the FK graph before touching grading/enrollment:

- `School_year` is the root scope; `Trimester` (3 per year, auto-created), `Course`, `Grade`, and
  `Ausencias` all FK to it.
- `Course` = a level (`Tipo`: Eso/Bachillerato/IB) + `Section` (e.g. "1A"), scoped to a school year.
- `Students` ↔ `Course` is many-to-many through `Students_Courses` (`unique_together` student+section).
- `Subjects_Courses` assigns a `Subject` + `Teacher` to a `Course` + `Trimester`, and carries an
  `assigned_course_sections` M2M to a **subset** of `Students_Courses` (which students take that subject).
- `Grade` (0–10 validated Decimal, plus `grade_type` and `grade_type_number`) and `Ausencias`
  (Ausencia/Retraso) both FK directly to student/subject/trimester/school_year and rely on
  `unique_together` to prevent dupes.
- `Profile` extends `auth.User` 1:1 with a `role` (professor/student/tutor/administrator), a
  `student` FK, and a `children` M2M for tutors. **Authorization is role-based via `Profile.role`**
  — check it when adding any view.

### Request flow
- **Entry / auth hub:** `loginPage` (`mainapp/views.py:30`, url name `login`, path `/`) redirects by `Profile.role`.
- **Role dashboards:** `student_detail` (`views.py:101`, student + tutor read-only),
  `teacher_dashboard` (`:273`), `class_dashboard` (`:411`), `adminage_dashboard_view` (`:1141`).
- **Admin setup flow** (multi-step, formset-driven): create school year → create courses/sections →
  assign subjects/teachers → create & assign students → reassign students. Views prefixed
  `create_*` / `assign_*` / `reassign_*`.
- **Cascading dropdowns** use jQuery + `ajax/*` and `load_*` endpoints (`urls.py`) returning JSON
  for year→course→section→student chains.
- **CSV:** export via `download_class_list` / `grades_csv` / `class_grades_download`; bulk grade
  import via the `import_grades` view (`views.py:1012`), which validates the 0–10 range and skips
  rows duplicating an existing grade.

### Frontend
Server-rendered Django templates + vanilla JS + jQuery. Global templates in `templates/`
(`base.html`, `navbar.html`, `sidebar.html`); app templates in `mainapp/templates/{mainapp,adminage}/`.
Static in `static/{css,js}/`. The `sidebar_extras.sidebar_courses` inclusion tag renders the course
sidebar per school year.

## Gotchas

- `mainapp/forms.py` defines **`GradeForm` twice** (`:10` full-featured, `:196` bare `__all__`);
  the second shadows the first at import. Verify which is active before editing grade forms.
- `settings.py` is dev-only: `DEBUG=True`, hardcoded `SECRET_KEY`, `ALLOWED_HOSTS=[]`, inline DB
  creds. No prod config (no Dockerfile/gunicorn/`.env`). Do not treat as production-ready.
- Only one migration (`0001_initial.py`) — schema changes are folded into it rather than added
  incrementally. Any new migration is a deliberate break from that pattern; ask first.
- `tutor_dashboard` (`views.py:604`) is deprecated and redirects to the student dashboard.

## Wiki (project knowledge base)

`wiki/` is a symlink to `~/atlas/wikis/tr/` (hosted in atlas since 2026-08-04, both control hosts).
It holds ~42 pages: the 17 security findings and what fixed them (DOM XSS, systemic BOLA, missing
authorization, mass assignment, forbidden-returns-200), the UI-overhaul design rules, and the log.

- Start at `wiki/hot.md`, then `wiki/index.md`; `wiki/log.md` is the chronological record.
- ⚠ The symlink is excluded via `.git/info/exclude`, **never `.gitignore`** — TR is public and the
  repo must be left untouched. Do not add `wiki/` to a tracked ignore file.
- Log findings, decisions and bug fixes back to the wiki, not only to the chat.

## Multi-machine

Two control hosts (macOS `~/TR`, Fedora). Single `main` lineage on both — TR does **not** use
Book-Ingestion's `linux`-branch model. `TR-step5` is a `git worktree` of this repo, not a separate
project.
