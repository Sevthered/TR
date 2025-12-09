#!/bin/bash

# --- Configuration ---
PROJECT_DIR="/Users/sebastian/TR" # Set your project's root directory
VENV_ACTIVATE="$PROJECT_DIR/venv/bin/activate"
SESSION_NAME="dev-session"
DELAY_SECONDS=3

# --- 1. Change to the Project Directory ---
echo "Changing directory to $PROJECT_DIR..."
cd "$PROJECT_DIR" || { echo "Error: Failed to change to $PROJECT_DIR. Exiting."; exit 1; }

# --- 2. Activate Venv and Find Command Paths (Using the DOT command) ---
echo "Sourcing virtual environment and determining command paths..."
if [ -f "$VENV_ACTIVATE" ]; then
    # *** FIX 2: Use the POSIX-compliant dot command ***
    . "$VENV_ACTIVATE"
else
    echo "ERROR: Virtual environment script not found at $VENV_ACTIVATE. Cannot proceed."
    exit 1
fi

# Find the full executable path for the commands (after venv activation)
PODMAN_COMPOSE_CMD=$(command -v podman-compose)

if [ -z "$PODMAN_COMPOSE_CMD" ]; then
    echo "ERROR: 'podman-compose' command not found! Check your venv dependencies."
    exit 1
fi

echo "Using podman-compose path: $PODMAN_COMPOSE_CMD"


# --- 3. Manage and Create the tmux Session ---

if tmux has-session -t $SESSION_NAME 2>/dev/null; then
    echo "Existing tmux session '$SESSION_NAME' found. Killing it..."
    tmux kill-session -t $SESSION_NAME
fi

echo "Starting tmux session '$SESSION_NAME'..."

# COMMAND 1 (Pane 1): Run podman-compose up
# Since podman-compose may be a system command, we use the explicitly found path.
tmux new-session -d -s $SESSION_NAME "$PODMAN_COMPOSE_CMD up"

# Wait for a brief moment to ensure the first pane starts
sleep 1

# COMMAND 2 (Pane 2): Wait, activate Venv, and then run runserver
echo "Creating second pane and pausing for $DELAY_SECONDS seconds before starting the web server..."

# *** FIX 3: Explicitly source the Venv INSIDE the tmux command ***
# This ensures the 'python manage.py runserver' uses the venv's python.
RUNSERVER_COMMAND=". \"$VENV_ACTIVATE\" && sleep $DELAY_SECONDS && echo '--- Delay complete. Starting runserver... ---' && python manage.py runserver"

tmux split-window -v -t $SESSION_NAME:0 "$RUNSERVER_COMMAND"

# --- 4. Attach to the tmux session ---
echo "Attaching to tmux session. Press **Ctrl+b** followed by **d** to detach."
tmux attach-session -t $SESSION_NAME