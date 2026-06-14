#!/usr/bin/env bash

# Stop execution if any command fails
set -e

echo "Starting HyVis update..."

# 1. Pull latest changes
if [ -d .git ]; then
    echo "Pulling latest code from Git..."
    git pull
else
    echo "Warning: No .git directory found. Skipping git pull."
fi

# 2. Check and activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "ERROR: Virtual environment (.venv) not found. Please create it first." >&2
    exit 1
fi

# 3. Reinstall package
echo "Installing package updates..."
pip install .

echo "----------------------------------------"
echo "HyVis updated successfully."
echo "----------------------------------------"