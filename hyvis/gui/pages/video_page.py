"""
video_page.py — Configuration page for [video] frame sampling and frame caching.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hyvis.config import AppConfig, VideoConfig
from hyvis.gui.base_widgets import FloatSpinBox, SmoothScrollArea
from hyvis.gui.pages.base import BaseConfigPage
from hyvis.gui.theme import TextRole
from hyvis.gui.widgets import (
    SectionCard,
    SemanticLabel,
    add_form_row,
    bind_field_metadata,
    setup_field_tooltip,
)
from hyvis.video.planner import FramePlanConfig, plan_frames


class VideoPage(BaseConfigPage):
    """Configuration page for [video] preprocessing settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_loading_ui: bool = False
        self._setup_ui()

    def refresh_theme(self) -> None:
        self._update_sampling_preview()

    def _setup_ui(self) -> None:
        app_fields = AppConfig.model_fields
        v_fields = VideoConfig.model_fields

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = SmoothScrollArea(self, scroll_margin=(8, 4))
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 0, 12)
        layout.setSpacing(12)

        # 1. Master Enable Card
        video_title = app_fields["video"].title or "Video & Animation"
        self.enable_card = SectionCard(
            title=video_title,
            tooltip=app_fields["video"].description or "",
            parent=container,
        )
        enable_layout = QVBoxLayout()
        enable_layout.setSpacing(6)

        enabled_title = v_fields["enabled"].title or "Enable Video & Animation Processing"
        self.enabled_chk = QCheckBox(enabled_title.replace("&", "&&"), self.enable_card)
        self.enabled_chk.setObjectName("enabled")
        setup_field_tooltip(self.enabled_chk, v_fields["enabled"])
        self.enabled_chk.setChecked(False)
        self.enabled_chk.toggled.connect(self._on_enabled_toggled)
        enable_layout.addWidget(self.enabled_chk)

        desc_lbl = SemanticLabel(
            "Samples representative frames from videos (MP4, WebM, MKV, etc.) and multi-frame animated images "
            "(GIF, APNG, WebP, AVIF, JXL, etc.), taking each tag's highest score across sampled frames.\n"
            "When disabled, videos and multi-frame animations are skipped so Frame 0 is never processed alone.",
            role=TextRole.SECONDARY,
            parent=self.enable_card,
        )
        desc_lbl.setWordWrap(True)
        enable_layout.addWidget(desc_lbl)

        note_lbl = SemanticLabel(
            "Note: HyVis doesn't currently support any tagging model that is capable of understanding temporal content / natively handling video input. "
            "Frame extraction here is thus more of a workaround to use static image taggers for video tagging.",
            role=TextRole.WARNING,
            parent=self.enable_card,
        )
        note_lbl.setWordWrap(True)
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
        self.density_k_spin.setRange(0.1, 20.0)
        self.density_k_spin.setValue(2.0)
        self.density_k_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "density_k", self.density_k_spin)

        self.min_frames_spin = QSpinBox(self.sampling_card)
        self.min_frames_spin.setRange(1, 512)
        self.min_frames_spin.setValue(4)
        self.min_frames_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "min_frames", self.min_frames_spin)

        self.max_frames_spin = QSpinBox(self.sampling_card)
        self.max_frames_spin.setRange(1, 2048)
        self.max_frames_spin.setValue(128)
        self.max_frames_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "max_frames", self.max_frames_spin)

        self.sparse_below_spin = QSpinBox(self.sampling_card)
        self.sparse_below_spin.setRange(1, 512)
        self.sparse_below_spin.setValue(32)
        self.sparse_below_spin.valueChanged.connect(lambda _: self._on_sampling_changed())
        add_form_row(sampling_form, v_fields, "sparse_below_frames", self.sparse_below_spin)

        sampling_outer.addLayout(sampling_form)

        # Live sampling formula preview
        self.preview_label = QLabel(self.sampling_card)
        self.preview_label.setWordWrap(True)
        sampling_outer.addWidget(self.preview_label)

        self.sampling_card.setContentLayout(sampling_outer)
        layout.addWidget(self.sampling_card)

        # 3. Frame Caching Card
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
        self.cache_mode_combo.setEditable(False)
        # todo: make the descriptions into extra json data for ui in config.py?
        # todo: could also help making comboboxes generate/populate more cleanly/automatically
        self.cache_mode_combo.addItem("Auto (Memory for 1 model, Disk for 2+)", userData="auto")
        self.cache_mode_combo.addItem("Memory Only (No disk writes; re-extracts per model)", userData="memory")
        self.cache_mode_combo.addItem("Disk Cache (Save frames until all models finish)", userData="disk")
        self.cache_mode_combo.currentIndexChanged.connect(lambda _: self._on_cache_mode_changed())
        add_form_row(cache_form, v_fields, "frame_cache_mode", self.cache_mode_combo)

        # Frame Cache Directory Row
        dir_box = QWidget(self.cache_card)
        dir_row = QHBoxLayout(dir_box)
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(6)

        self.cache_dir_edit = QLineEdit(dir_box)
        bind_field_metadata(self.cache_dir_edit, v_fields["frame_cache_dir"], set_placeholder=False)
        self.cache_dir_edit.setPlaceholderText("Auto (frame_cache directory beside database path)")
        self.cache_dir_edit.textChanged.connect(lambda _: self._on_field_changed())
        dir_row.addWidget(self.cache_dir_edit, stretch=1)

        self.browse_cache_dir_btn = QPushButton("Browse Folder...", dir_box)
        self.browse_cache_dir_btn.clicked.connect(self._on_browse_cache_dir)
        dir_row.addWidget(self.browse_cache_dir_btn)

        self._cache_dir_label = add_form_row(cache_form, v_fields, "frame_cache_dir", dir_box)
        self._cache_dir_box = dir_box

        cache_outer.addLayout(cache_form)

        self.cache_note_label = SemanticLabel(
            "Note: When disk caching is active on multi-model runs, the first model receives the original "
            "in-memory decoded frames while subsequent models read the disk-cached frames. "
            "Temporary frame folders are deleted per file as soon as all models finish that file.",
            role=TextRole.SECONDARY,
            parent=self.cache_card,
        )
        self.cache_note_label.setWordWrap(True)
        cache_outer.addWidget(self.cache_note_label)

        self.cache_card.setContentLayout(cache_outer)
        layout.addWidget(self.cache_card)

        layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll)

        self._sync_enabled_states()
        self._update_sampling_preview()

    # region Handlers & Live Helpers

    def _sync_enabled_states(self) -> None:
        is_enabled = self.enabled_chk.isChecked()
        self.sampling_card.setEnabled(is_enabled)
        self.cache_card.setEnabled(is_enabled)

        mode = str(self.cache_mode_combo.currentData() or "auto")
        dir_active = is_enabled and (mode != "memory")
        self._cache_dir_label.setEnabled(dir_active)
        self._cache_dir_box.setEnabled(dir_active)

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

    def _on_enabled_toggled(self, _checked: bool) -> None:
        self._sync_enabled_states()
        self._on_field_changed()

    def _on_sampling_changed(self) -> None:
        self._update_sampling_preview()
        self._on_field_changed()

    def _on_cache_mode_changed(self) -> None:
        self._sync_enabled_states()
        self._on_field_changed()

    def _on_field_changed(self) -> None:
        if self._is_loading_ui:
            return
        self.changed.emit()

    def _on_browse_cache_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Frame Cache Directory")
        if folder:
            self.cache_dir_edit.setText(folder)
            self._on_field_changed()

    # endregion

    # region Config Lifecycle

    def load_config(self, cfg: AppConfig) -> None:
        self._is_loading_ui = True
        try:
            v = cfg.video
            self.enabled_chk.setChecked(v.enabled)
            self.density_k_spin.setValue(float(v.density_k))
            self.min_frames_spin.setValue(int(v.min_frames))
            self.max_frames_spin.setValue(int(v.max_frames))
            self.sparse_below_spin.setValue(int(v.sparse_below_frames))

            idx = self.cache_mode_combo.findData(v.frame_cache_mode)
            self.cache_mode_combo.setCurrentIndex(max(idx, 0))
            self.cache_dir_edit.setText(v.frame_cache_dir or "")

            self._sync_enabled_states()
            self._update_sampling_preview()
        finally:
            self._is_loading_ui = False

    def apply_to_dict(self, data: dict[str, Any]) -> None:
        v_dict: dict[str, Any] = data.setdefault("video", {})
        v_dict["enabled"] = self.enabled_chk.isChecked()
        v_dict["density_k"] = float(self.density_k_spin.value())
        v_dict["min_frames"] = int(self.min_frames_spin.value())
        v_dict["max_frames"] = int(self.max_frames_spin.value())
        v_dict["sparse_below_frames"] = int(self.sparse_below_spin.value())
        v_dict["frame_cache_mode"] = str(self.cache_mode_combo.currentData() or "auto")
        v_dict["frame_cache_dir"] = self.cache_dir_edit.text().strip() or None

    # endregion
