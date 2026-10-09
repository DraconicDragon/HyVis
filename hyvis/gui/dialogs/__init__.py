"""Dialog components for the HyVis desktop interface."""

from .about_dialog import AboutDialog
from .launch_dialog import LaunchDialog
from .preferences_dialog import PreferencesDialog
from .unsaved_changes_dialog import (
    UnsavedChangesAction,
    UnsavedChangesDialog,
    prompt_unsaved_changes,
)

__all__ = [
    "AboutDialog",
    "LaunchDialog",
    "PreferencesDialog",
    "UnsavedChangesAction",
    "UnsavedChangesDialog",
    "prompt_unsaved_changes",
]
