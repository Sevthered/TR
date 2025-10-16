#!/bin/bash
scc . \
  --exclude-dir "venv,__pycache__,node_modules" \
  --exclude-ext "min.js,min.css,pyc,sqlite3,yaml,yml" \
  --no-cocomo \
  --no-min-gen \
  --no-duplicates