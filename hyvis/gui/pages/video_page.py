"""
video_page.py — Configuration page for [video] frame sampling, caching, and threshold adjustment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import (
    FRAME_CACHE_EFFORT_RANGES,
    LOSSLESS_CACHE_FORMATS,
    AppConfig,
    VideoConfig,
    adjust_threshold,
)
from hyvis.gui.base_widgets import FloatSpinBox, SemanticLabel
from hyvis.gui.pages.base import BaseConfigPage
from hyvis.gui.schema_binding import add_form_row, bind_field_metadata, set_combo_value
from hyvis.gui.theme import TextRole
from hyvis.gui.widgets import PathPickerWidget, SectionCard
from hyvis.video.planner import FramePlanConfig, plan_frames


class VideoPage(BaseConfigPage):
    """Configuration page for [video] preprocessing settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._db_path_hint: str = "data/hyvis.db"
        self._setup_ui()

    def refresh_theme(self) -> None:
        self._update_sampling_preview()
        self._update_threshold_preview()

    def _setup_ui(self) -> None:
        app_fields = AppConfig.model_fields
        v_fields = VideoConfig.model_fields

        container, layout = self.create_scroll_layout()

        # 1. Master Enable Card
        self.enable_card = SectionCard(field_info=app_fields["video"], parent=container)
        enable_layout = QVBoxLayout()
        enable_layout.setSpacing(6)

        self.enabled_chk = QCheckBox(self.enable_card)
        bind_field_metadata(self.enabled_chk, v_fields["enabled"], field_name="enabled")
        self.enabled_chk.toggled.connect(self._on_enabled_toggled)
        enable_layout.addWidget(self.enabled_chk)

        desc_lbl = SemanticLabel(
            "Samples representative frames from videos (MP4, WebM, MKV, etc.) and multi-frame animated images "
            "(GIF, APNG, WebP, AVIF, JXL, etc.), taking each tag's highest score across sampled frames.\n"
            "When disabled, videos and multi-frame animations are skipped so Frame 0 is never processed alone.",
            role=TextRole.SECONDARY,
            parent=self.enable_card,
        )
        enable_layout.addWidget(desc_lbl)

        note_lbl = SemanticLabel(
            "Note: HyVis doesn't currently support any tagging model that is capable of understanding temporal content / natively handling video input. "
            "Frame extraction here is thus more of a workaround to use static image taggers for video tagging.",
            role=TextRole.WARNING,
            parent=self.enable_card,
        )
        enable_layout.addWidget(note_lbl)

        self.enable_card.setContentLayout(enable_layout)
        layout.addWidget(self.enable_card)

        # 2. Frame Sampling Card
        self.sampling_card = SectionCard(
            title="Frame Sampling",
            tooltip="Controls how many frames are sampled across short animations and long videos.",
            parent=container,
        )
        sampling_outer = QVBoxLayout()
        sampling_outer.setSpacing(8)

        sampling_form = QFormLayout()
        sampling_form.setSpacing(8)

        self.density_k_spin = FloatSpinBox(self.sampling_card)
        self.density_k_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "density_k", self.density_k_spin)

        self.min_frames_spin = QSpinBox(self.sampling_card)
        self.min_frames_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "min_frames", self.min_frames_spin)

        self.max_frames_spin = QSpinBox(self.sampling_card)
        self.max_frames_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "max_frames", self.max_frames_spin)

        self.sparse_below_spin = QSpinBox(self.sampling_card)
        self.sparse_below_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "sparse_below_frames", self.sparse_below_spin)

        sampling_outer.addLayout(sampling_form)

        # Live sampling formula preview
        self.preview_label = QLabel(self.sampling_card)
        self.preview_label.setWordWrap(True)
        sampling_outer.addWidget(self.preview_label)

        self.sampling_card.setContentLayout(sampling_outer)
        layout.addWidget(self.sampling_card)

        # 3. Output Threshold Adjustment Card
        self.threshold_card = SectionCard(
            title="Output Threshold Adjustment",
            tooltip="Optionally tighten or loosen output thresholds specifically for videos and multi-frame animations.",
            parent=container,
        )
        thresh_outer = QVBoxLayout()
        thresh_outer.setSpacing(8)

        thresh_form = QFormLayout()
        thresh_form.setSpacing(8)

        self.thresh_mode_combo = QComboBox(self.threshold_card)
        self.thresh_mode_combo.currentIndexChanged.connect(lambda _: self._on_threshold_changed())
        add_form_row(thresh_form, v_fields, "threshold_adjust_mode", self.thresh_mode_combo)

        self.thresh_val_spin = FloatSpinBox(self.threshold_card)
        self.thresh_val_spin.valueChanged.connect(lambda _: self._on_threshold_changed())
        add_form_row(thresh_form, v_fields, "threshold_adjust_value", self.thresh_val_spin)

        thresh_outer.addLayout(thresh_form)

        self.thresh_preview_label = QLabel(self.threshold_card)
        self.thresh_preview_label.setWordWrap(True)
        thresh_outer.addWidget(self.thresh_preview_label)

        self.threshold_card.setContentLayout(thresh_outer)
        layout.addWidget(self.threshold_card)

        # 4. Frame Caching Card
        self.cache_card = SectionCard(
            title="Frame Caching",
            tooltip="Controls whether extracted frames are streamed in memory or temporarily cached on disk for multi-model runs.",
            parent=container,
        )
        cache_outer = QVBoxLayout()
        cache_outer.setSpacing(8)

        cache_form = QFormLayout()
        cache_form.setSpacing(8)

        self.cache_mode_combo = QComboBox(self.cache_card)
        self.cache_mode_combo.currentIndexChanged.connect(lambda _: self._on_cache_setting_changed())
        add_form_row(cache_form, v_fields, "frame_cache_mode", self.cache_mode_combo)

        self.cache_format_combo = QComboBox(self.cache_card)
        self.cache_format_combo.currentIndexChanged.connect(lambda _: self._on_cache_setting_changed())
        self._cache_format_label = add_form_row(cache_form, v_fields, "frame_cache_format", self.cache_format_combo)

        self.cache_quality_spin = QSpinBox(self.cache_card)
        self.cache_quality_spin.valueChanged.connect(lambda _: self.emit_changed())
        self._cache_quality_label = add_form_row(cache_form, v_fields, "frame_cache_quality", self.cache_quality_spin)

        self.cache_effort_spin = QSpinBox(self.cache_card)
        self.cache_effort_spin.valueChanged.connect(lambda _: self.emit_changed())
        self._cache_effort_label = add_form_row(cache_form, v_fields, "frame_cache_effort", self.cache_effort_spin)

        # Frame Cache Directory Row
        self.cache_dir_picker = PathPickerWidget(
            mode="directory",
            dialog_title="Select Frame Cache Directory",
            empty_display=self._auto_frame_cache_display,
            parent=self.cache_card,
        )
        self.cache_dir_picker.setPlaceholderText("Auto (frame_cache directory beside database path)")
        self.cache_dir_picker.textChanged.connect(lambda _: self.emit_changed())
        self._cache_dir_label = add_form_row(
            cache_form, v_fields, "frame_cache_dir", self.cache_dir_picker, set_placeholder=False
        )

        cache_outer.addLayout(cache_form)

        self.cache_note_label = SemanticLabel(
            "Note: When disk caching is active on multi-model runs, the first model receives the original "
            "in-memory decoded frames while subsequent models read the disk-cached frames. "
            "Temporary frame folders are deleted per file as soon as all models finish that file.",
            role=TextRole.SECONDARY,
            parent=self.cache_card,
        )
        cache_outer.addWidget(self.cache_note_label)

        self.cache_card.setContentLayout(cache_outer)
        layout.addWidget(self.cache_card)

        layout.addStretch(1)

        self._sync_enabled_states()
        self._update_sampling_preview()
        self._update_threshold_preview()

    # region Handlers & Live Helpers

    def _auto_frame_cache_display(self) -> str:
        db_raw = self._db_path_hint.strip() or "data/hyvis.db"
        db_abs = Path(os.path.abspath(os.path.expanduser(db_raw)))
        return f"Resolves to: {db_abs.parent / 'frame_cache'} (auto beside database.path)"

    def set_database_path(self, db_path: str) -> None:
        """Update the fallback database.path hint when edited on AppDbPage."""
        self._db_path_hint = db_path.strip() or "data/hyvis.db"
        self.cache_dir_picker.refresh_resolved_path()

    def _sync_enabled_states(self) -> None:
        is_enabled = self.enabled_chk.isChecked()
        self.sampling_card.setEnabled(is_enabled)
        self.cache_card.setEnabled(is_enabled)
        self.threshold_card.setEnabled(is_enabled)

        mode = str(self.cache_mode_combo.currentData() or "auto")
        disk_possible = is_enabled and (mode != "memory")
        fmt = str(self.cache_format_combo.currentData() or "jpeg")
        is_lossless = fmt in LOSSLESS_CACHE_FORMATS
        effort_bounds = FRAME_CACHE_EFFORT_RANGES.get(fmt)

        self._cache_format_label.setEnabled(disk_possible)
        self.cache_format_combo.setEnabled(disk_possible)

        quality_active = disk_possible and (not is_lossless)
        self._cache_quality_label.setEnabled(quality_active)
        self.cache_quality_spin.setEnabled(quality_active)
        if is_lossless:
            self.cache_quality_spin.setToolTip("Not used for lossless frame cache formats.")
        else:
            self.cache_quality_spin.setToolTip(str(VideoConfig.model_fields["frame_cache_quality"].description))

        if effort_bounds is not None:
            lo, hi = effort_bounds
            was_blocked = self.cache_effort_spin.signalsBlocked()
            self.cache_effort_spin.blockSignals(True)
            self.cache_effort_spin.setRange(lo, hi)
            self.cache_effort_spin.blockSignals(was_blocked)

            self._cache_effort_label.setEnabled(disk_possible)
            self.cache_effort_spin.setEnabled(disk_possible)
        else:
            self._cache_effort_label.setEnabled(False)
            self.cache_effort_spin.setEnabled(False)

        self._cache_dir_label.setEnabled(disk_possible)
        self.cache_dir_picker.setEnabled(disk_possible)

    def _update_sampling_preview(self) -> None:
        min_f = self.min_frames_spin.value()
        max_f = self.max_frames_spin.value()

        if min_f > max_f:
            self.preview_label.setText("⚠ Minimum Sampled Frames cannot exceed Maximum Sampled Frames.")
            self.preview_label.setStyleSheet(f"color: {self.tokens.status_danger}; font-weight: 500;")
            return

        cfg = FramePlanConfig(
            density_k=float(self.density_k_spin.value()),
            min_frames=min_f,
            max_frames=max_f,
            sparse_below_frames=self.sparse_below_spin.value(),
        )
        samples = [
            ("5s", 5.0),
            ("30s", 30.0),
            ("2m", 120.0),
            ("10m", 600.0),
            ("1h", 3600.0),
        ]
        parts = [f"{lbl} → {plan_frames(dur, int(dur * 24), config=cfg).target_count}" for lbl, dur in samples]
        self.preview_label.setText("Sample preview (24 fps):  " + "  |  ".join(parts) + " frames")
        self.preview_label.setStyleSheet(f"color: {self.tokens.text_muted};")

    def _update_threshold_preview(self) -> None:
        mode = str(self.thresh_mode_combo.currentData() or "headroom")
        val = float(self.thresh_val_spin.value())

        if abs(val) < 1e-6:
            self.thresh_preview_label.setText("No adjustment active (uses Output Filter thresholds as-is).")
            self.thresh_preview_label.setStyleSheet(f"color: {self.tokens.text_muted};")
            return

        direction = "Stricter" if val > 0 else "More permissive"
        decimals = self.thresh_val_spin.decimals()
        bases = (0.10, 0.27, 0.40, 0.75, 0.85)
        examples = "  |  ".join(f"{b:.2f} → {adjust_threshold(b, mode, val):.{decimals}f}" for b in bases)
        self.thresh_preview_label.setText(f"{direction} ({mode}, k={val:+.{decimals}f}):  {examples}")
        self.thresh_preview_label.setStyleSheet(f"color: {self.tokens.text_muted};")

    def _on_enabled_toggled(self, _checked: bool) -> None:
        self._sync_enabled_states()
        self.emit_changed()

    def _on_sampling_changed(self) -> None:
        self._update_sampling_preview()
        self.emit_changed()

    def _on_cache_setting_changed(self) -> None:
        self._sync_enabled_states()
        self.emit_changed()

    def _on_threshold_changed(self) -> None:
        self._update_threshold_preview()
        self.emit_changed()

    # endregion

    # region Config Lifecycle

    def load_config(self, cfg: AppConfig) -> None:
        with self.suppress_changes():
            v = cfg.video
            self._db_path_hint = cfg.database.path
            self.enabled_chk.setChecked(v.enabled)
            self.density_k_spin.setValue(float(v.density_k))
            self.min_frames_spin.setValue(int(v.min_frames))
            self.max_frames_spin.setValue(int(v.max_frames))
            self.sparse_below_spin.setValue(int(v.sparse_below_frames))

            set_combo_value(self.cache_mode_combo, v.frame_cache_mode)
            set_combo_value(self.cache_format_combo, v.frame_cache_format)
            self._sync_enabled_states()  # Apply format-specific effort bounds before setting value
            self.cache_quality_spin.setValue(int(v.frame_cache_quality))
            self.cache_effort_spin.setValue(int(v.frame_cache_effort))
            self.cache_dir_picker.setText(v.frame_cache_dir or "")
            self.cache_dir_picker.refresh_resolved_path()

            set_combo_value(self.thresh_mode_combo, v.threshold_adjust_mode)
            self.thresh_val_spin.setValue(float(v.threshold_adjust_value))

            self._update_sampling_preview()
            self._update_threshold_preview()

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        v_dict: dict[str, Any] = data.setdefault("video", {})
        v_dict["enabled"] = self.enabled_chk.isChecked()
        v_dict["density_k"] = float(self.density_k_spin.value())
        v_dict["min_frames"] = int(self.min_frames_spin.value())
        v_dict["max_frames"] = int(self.max_frames_spin.value())
        v_dict["sparse_below_frames"] = int(self.sparse_below_spin.value())
        v_dict["frame_cache_mode"] = str(self.cache_mode_combo.currentData() or "auto")
        v_dict["frame_cache_format"] = str(self.cache_format_combo.currentData() or "jpeg")
        v_dict["frame_cache_quality"] = int(self.cache_quality_spin.value())
        v_dict["frame_cache_effort"] = int(self.cache_effort_spin.value())
        v_dict["frame_cache_dir"] = self.cache_dir_picker.text().strip() or None
        v_dict["threshold_adjust_mode"] = str(self.thresh_mode_combo.currentData() or "headroom")
        v_dict["threshold_adjust_value"] = float(self.thresh_val_spin.value())

    # endregion
