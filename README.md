# TR Project

> ### ⚠️ Scope of the assessed work
>
> **TR** here means *Treball de Recerca* — the individual research project each student in
> Catalonia carries out for school. The work that was handed in and assessed is this repository
> **up to and including commit `93b73e0` (15 December 2025)**.
>
> **Every commit after `93b73e0` was made later and is outside the original project.** That later
> work — a security remediation, a UI rebuild, an automated test suite and CI — was written months
> after the hand-in, with AI assistance, and formed no part of what was graded. The
> `ai-assisted-ui-overhaul` branch is later work in its entirety: it was cut from `93b73e0` and is
> not merged into `main`.
>
> **From commit `43a3d11` onward, work done outside school is brought onto `main`.** Until then
> `main` held only the assessed project plus documentation about it. `43a3d11` and `8f8950e` are
> the first commits to carry later *application* code here — a security remediation written months
> after the hand-in, on a personal branch, with AI assistance. They arrived by cherry-pick, not by
> merging the branch, which is why `8f8950e` records the commit it came from.
>
> To read the project exactly as it was submitted:
>
> ```bash
> git checkout 93b73e0
> ```

## Overview
My **TR** consists in a comprehensive school management web application built with **Django**. It provides a digital platform for managing the academic lifecycle, including student enrollment, course management, grading, and attendance tracking. The system is designed to serve multiple user roles: Administrators, Professors, Tutors (Parents), and Students.

## Technology Stack
-   **Backend Framework**: Python / Django 5.2
-   **Database**: PostgreSQL (running in Docker)
-   **Frontend**: HTML5, CSS3, JavaScript (Vanilla + jQuery)
-   **Containerization**: Docker / Podman
-   **Environment Management**: `venv` (Python Virtual Environment)

## Key Features

### 👥 User Roles & Permissions
-   **Administrators**: Full control over the system. Can create school years, define course structures, and manage users.
-   **Professors**: Access to assigned classes. Can grade students, record absences, and download/upload class data.
-   **Tutors (Parents)**: View-only access to their children's academic records (grades, absences).
-   **Students**: Personal dashboard to view their own grades and attendance history.

### 🏫 Academic Management
-   **School Years & Trimesters**: Flexible system to define academic years (e.g., "2025-2026") and their associated trimesters.
-   **Course Structure**: Support for different educational levels (ESO, Bachillerato, IB) and sections (e.g., "1A", "2B").
-   **Cascading Logic**: Smart forms that filter levels and sections dynamically based on the selected course type.

### 📊 Grading & Attendance
-   **Gradebook**: Record grades for exams, partials, and finals.
-   **Attendance Tracking**: Log absences and delays with timestamps.
-   **Data Export/Import**:
    -   Download class lists and grade templates as CSV.
    -   Bulk import grades via CSV upload.

## Project Structure
```text
TR/
├── mainapp/            # Core application logic
│   ├── models.py       # Database schemas (Students, Courses, Grades, etc.)
│   ├── views.py        # Business logic and request handling
│   ├── forms.py        # Form definitions and validations
│   └── templates/      # HTML templates for the UI
├── tr_webpage/         # Project configuration (settings, urls)
├── static/             # Static assets (CSS, JS, Images)
├── templates/          # Global templates (base.html, navbar, sidebar)
├── compose.yaml        # Docker Compose configuration for PostgreSQL
└── run.sh              # Helper script to launch the environment
```

## Getting Started

### Prerequisites

*   **Python 3.10+**
*   **Podman** (for running the database)
*   **Tmux** (optional, for using the `run.sh` script)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd TR
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    Use `requirements-dev.txt` instead if you want `podman-compose` and the
    `Faker`-based seed command as well.

4.  **Create your `.env`:**
    The application no longer carries a `SECRET_KEY` or a database password in
    source. Both are read from the environment, and **it will not start without
    them**.
    ```bash
    cp .env.example .env
    ```
    Then fill in `SECRET_KEY` and `POSTGRES_PASSWORD`. `.env.example` documents
    every variable and includes the command for generating a key.

### Running the Application

You can run the application using the provided script or manually.

#### Option 1: Using `run.sh` (Recommended)

This script manages the database container and the Django server in a `tmux` session.

```bash
./run.sh
```

*   **What it does:**
    1.  Starts the PostgreSQL database using `podman-compose`.
    2.  Starts the Django development server.
    3.  Attaches to a `tmux` session where you can see both logs.
*   **To detach:** Press `Ctrl+b` then `d`.
*   **To stop:** Kill the tmux session or press `Ctrl+c` in the panes.

#### Option 2: Manual Start

1.  **Start the Database:**
    Ensure you have Podman installed.
    ```bash
    podman-compose up -d
    ```

2.  **Run Migrations (First time only):**
    ```bash
    python manage.py migrate
    ```

3.  **Create Superuser (First time only):**
    ```bash
    python manage.py createsuperuser
    ```

4.  **Start the Server:**
    ```bash
    python manage.py runserver
    ```

Access the application at `http://127.0.0.1:8000/`.

2.  **Access the Admin Panel**:
    -   URL: `http://127.0.0.1:8000/admin`
    -   Use the superuser credentials to log in (default: `admin` / `admin` - *check `compose.yaml` for DB creds, Django admin user may need creation*).

## Development Notes
-   **AJAX**: The application uses jQuery for dynamic form interactions (e.g., selecting a course type loads the available levels).
-   **Security**: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` and the database credentials all come from
    the environment (see `.env.example`). A remediation round in `8f8950e` added a Content Security
    Policy, login throttling and rate limiting, secure cookie and transport flags, an audit log, and
    fixes for a DOM XSS and a Django-admin privilege escalation.

    > ⚠️ **The remediation is partial, and this branch is not hardened.** Two findings rated
    > *critical* are still open here: there is no object-level authorization (any authenticated user
    > can reach another user's records by changing an id in the URL), and several endpoints check
    > only that you are logged in, not who you are. Both were fixed on `ai-assisted-ui-overhaul`,
    > but only as part of the UI rewrite, so neither could be cherry-picked on its own. `GradeForm`
    > is also still declared twice, the second shadowing the first, and denials still render with
    > HTTP 200. **Do not deploy this branch to a public network.**
