from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Callable

if sys.platform.startswith("win") and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "windows:fontengine=freetype"

try:
    from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QFont, QKeyEvent, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QFormLayout,
        QGraphicsOpacityEffect,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PySide6 is required to run the GUI. Install it with: pip install PySide6") from exc

from . import APP_NAME, APP_TITLE
from .core import (
    BuildOptions,
    DEFAULT_ZIP_COMPRESS_LEVEL,
    PlateJob,
    ThreeMfSummary,
    build_packed_3mf,
    format_duration,
    format_filament,
    list_gcode_members,
    list_swap_gcode_files,
    read_3mf_summary,
)
from .builder import preview_members_for_gcode_member, resolve_output_gcode_member
from .paths import default_patch_config_path, default_swap_gcode_dir, user_settings_path
from .planning import (
    DEFAULT_OUTPUT_PATTERN,
    OutputNamingOptions,
    OutputSummary,
    make_unique_for_run,
    resolve_output_path,
    summarize_jobs_for_output,
    three_mf_summary_from_mapping,
)

SUMMARY_ROLE = Qt.UserRole + 100
PATH_ROLE = Qt.UserRole + 101

ORDER_COLUMN = 0
FILE_COLUMN = 1
COPIES_COLUMN = 2
TIME_COLUMN = 3
FILAMENT_COLUMN = 4

PREVIEW_IMAGE_RE = re.compile(r"^Metadata/plate_(\d+)(?:_small)?\.png$", re.IGNORECASE)


def preview_image_sort_key(member_name: str) -> tuple[int, int, str]:
    match = PREVIEW_IMAGE_RE.match(member_name)
    plate_number = int(match.group(1)) if match else 9999
    is_small = 1 if member_name.lower().endswith("_small.png") else 0
    return (is_small, plate_number, member_name.lower())


def first_preview_image_member(member_names: list[str], gcode_member: str | None = None) -> str | None:
    available_members = set(member_names)
    if gcode_member is not None:
        candidates = [name for name in preview_members_for_gcode_member(gcode_member) if name in available_members]
        if candidates:
            return sorted(candidates, key=preview_image_sort_key)[0]
    candidates = [name for name in member_names if PREVIEW_IMAGE_RE.match(name)]
    if not candidates:
        candidates = [
            name
            for name in member_names
            if name.lower().startswith("metadata/") and name.lower().endswith(".png")
        ]
    if not candidates:
        return None
    return sorted(candidates, key=preview_image_sort_key)[0]


class SuccessToast(QLabel):
    FADE_MS = 1000
    HOLD_MS = 5000

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            """
            QLabel {
                background: #f4fff6;
                color: #183f22;
                border: 3px solid #2e7d32;
                border-radius: 8px;
                padding: 20px 32px;
                font-size: 18px;
                font-weight: 700;
            }
            """
        )
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_in = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_in.setDuration(self.FADE_MS)
        self._fade_in.setEasingCurve(QEasingCurve.InOutQuad)
        self._fade_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_out.setDuration(self.FADE_MS)
        self._fade_out.setEasingCurve(QEasingCurve.InOutQuad)
        self._fade_out.finished.connect(self.hide)
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._start_fade_out)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, message: str) -> None:
        self._hold_timer.stop()
        self._fade_in.stop()
        self._fade_out.stop()
        self.setText(message)
        self._fit_to_parent()
        self._position()
        self._opacity_effect.setOpacity(0.0)
        self.show()
        self.raise_()
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.start()
        self._hold_timer.start(self.FADE_MS + self.HOLD_MS)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Resize and self.isVisible():
            self._fit_to_parent()
            self._position()
        return super().eventFilter(watched, event)

    def _fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setMaximumWidth(max(360, min(720, parent.width() - 48)))
        self.adjustSize()

    def _position(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 24
        x = max(margin, (parent.width() - self.width()) // 2)
        y = max(margin, parent.height() - self.height() - margin)
        self.move(x, y)

    def _start_fade_out(self) -> None:
        self._fade_out.stop()
        self._fade_out.setStartValue(self._opacity_effect.opacity())
        self._fade_out.setEndValue(0.0)
        self._fade_out.start()


class DropTableWidget(QTableWidget):
    def __init__(
        self,
        on_files_dropped: Callable[[list[Path]], None],
        on_delete_pressed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.on_files_dropped = on_files_dropped
        self.on_delete_pressed = on_delete_pressed
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Delete and self.selectedIndexes():
            self.on_delete_pressed()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched is self.viewport():
            if event.type() in {QEvent.DragEnter, QEvent.DragMove}:
                drag_event = event  # type: ignore[assignment]
                if self._has_3mf_urls(drag_event):
                    drag_event.acceptProposedAction()
                    return True
            if event.type() == QEvent.Drop:
                drop_event = event  # type: ignore[assignment]
                paths = self._paths_from_urls(drop_event.mimeData().urls())
                if paths:
                    self.on_files_dropped(paths)
                    drop_event.acceptProposedAction()
                    return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._has_3mf_urls(event):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._has_3mf_urls(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._paths_from_urls(event.mimeData().urls())
        if paths:
            self.on_files_dropped(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _has_3mf_urls(self, event: Any) -> bool:
        if not event.mimeData().hasUrls():
            return False
        return bool(self._paths_from_urls(event.mimeData().urls()))

    def _paths_from_urls(self, urls: list[QUrl]) -> list[Path]:
        result: list[Path] = []
        for url in urls:
            local_path = url.toLocalFile()
            if not local_path:
                continue
            path = Path(local_path)
            if path.is_dir():
                result.extend(sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() == ".3mf"))
            elif path.is_file() and path.suffix.lower() == ".3mf":
                result.append(path)
        return result


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(960, 860)
        self.setAcceptDrops(True)
        self._updating_table = False
        self._loading_settings = True
        self._settings = self.load_settings()
        self._shared_growth_enabled = False
        self.build_ui()
        self.load_swap_gcode_to_combo()
        self.restore_settings_to_ui()
        self.connect_option_signals()
        self._loading_settings = False
        self.update_total_summary()
        self.update_output_preview()

    def load_settings(self) -> dict[str, Any]:
        path = user_settings_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def save_settings(self) -> None:
        path = user_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")

    def build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        self.root_layout = root

        file_group = QGroupBox("Input 3MF files")
        self.file_group = file_group
        file_layout = QVBoxLayout(file_group)
        file_body = QHBoxLayout()
        table_layout = QVBoxLayout()
        self.table = DropTableWidget(self.add_paths, self.remove_selected)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Order", "3MF file", "Copies", "Time", "Filament"])
        self.table.horizontalHeader().setSectionResizeMode(ORDER_COLUMN, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(FILE_COLUMN, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COPIES_COLUMN, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(TIME_COLUMN, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(FILAMENT_COLUMN, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(ORDER_COLUMN, 104)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(220)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setToolTip("Drag .3mf files or folders here. Folders add all top-level .3mf files.")
        self.table.itemChanged.connect(self.on_table_item_changed)
        self.table.itemSelectionChanged.connect(self.update_thumbnail_preview)
        table_layout.addWidget(self.table)

        self.total_summary_label = QLabel("Total: 0 plates | Time: Unknown | Filament: Unknown")
        table_layout.addWidget(self.total_summary_label)
        file_body.addLayout(table_layout, 1)

        preview_group = QGroupBox("Selected thumbnail")
        preview_layout = QVBoxLayout(preview_group)
        self.thumbnail_label = QLabel("Select an input file")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setFixedSize(280, 220)
        self.thumbnail_label.setStyleSheet(
            """
            QLabel {
                background: #f8f8f8;
                border: 1px solid #cfcfcf;
                color: #666;
            }
            """
        )
        self.thumbnail_name_label = QLabel("")
        self.thumbnail_name_label.setWordWrap(True)
        self.thumbnail_name_label.setMaximumWidth(280)
        preview_layout.addWidget(self.thumbnail_label)
        preview_layout.addWidget(self.thumbnail_name_label)
        preview_layout.addStretch(1)
        file_body.addWidget(preview_group)
        file_layout.addLayout(file_body)

        file_buttons = QHBoxLayout()
        add_button = QPushButton("Add 3MF")
        remove_button = QPushButton("Remove")
        remove_all_button = QPushButton("Remove All")
        apply_default_copies_button = QPushButton("Apply Default Copies to Selected")
        self.build_button = QPushButton("Build 3MF")
        self.build_button.setMinimumHeight(64)
        self.build_button.setMinimumWidth(180)
        build_font = self.build_button.font()
        build_font.setPointSize(build_font.pointSize() + 2)
        build_font.setBold(True)
        self.build_button.setFont(build_font)

        add_button.clicked.connect(self.add_files)
        remove_button.clicked.connect(self.remove_selected)
        remove_all_button.clicked.connect(self.remove_all)
        apply_default_copies_button.clicked.connect(self.apply_default_copies_to_selected)
        self.build_button.clicked.connect(self.build_output)

        for button in (add_button, remove_button, remove_all_button, apply_default_copies_button):
            button.setMinimumHeight(44)
            file_buttons.addWidget(button)
        file_buttons.addStretch(1)
        file_buttons.addWidget(self.build_button)
        file_layout.addLayout(file_buttons)
        root.addWidget(file_group, 1)

        options_group = QGroupBox("Packing options")
        options_layout = QFormLayout(options_group)

        self.swap_gcode_combo = QComboBox()
        self.swap_gcode_combo.setMinimumWidth(260)
        self.swap_gcode_combo.setMaximumWidth(440)
        swap_gcode_row = QHBoxLayout()
        refresh_button = QPushButton("Refresh")
        open_folder_button = QPushButton("Open Folder")
        refresh_button.setFixedWidth(refresh_button.sizeHint().width())
        open_folder_button.setFixedWidth(open_folder_button.sizeHint().width())
        refresh_button.clicked.connect(self.load_swap_gcode_to_combo)
        open_folder_button.clicked.connect(self.open_swap_gcode_folder)
        swap_gcode_row.addWidget(self.swap_gcode_combo, 1)
        swap_gcode_row.addWidget(refresh_button)
        swap_gcode_row.addWidget(open_folder_button)
        options_layout.addRow("Swap G-code", swap_gcode_row)

        self.default_copies_spin = QSpinBox()
        self.default_copies_spin.setRange(1, 9999)
        self.default_copies_spin.setValue(1)
        self.default_copies_spin.setFixedWidth(96)
        options_layout.addRow("Default copies for new inputs", self.default_copies_spin)

        self.bed_cooldown_check = QCheckBox("Wait for bed cooldown")
        self.bed_cooldown_check.setChecked(True)
        self.cool_bed_spin = QSpinBox()
        self.cool_bed_spin.setRange(0, 120)
        self.cool_bed_spin.setValue(45)
        bed_row = QHBoxLayout()
        bed_row.addWidget(self.bed_cooldown_check)
        bed_row.addWidget(self.cool_bed_spin)
        bed_row.addWidget(QLabel("°C"))
        bed_row.addStretch(1)
        options_layout.addRow("Bed cooldown", bed_row)

        self.wait_spin = QSpinBox()
        self.wait_spin.setRange(0, 3600)
        self.wait_spin.setValue(45)
        self.wait_spin.setSuffix(" s")
        self.wait_spin.setFixedWidth(96)
        options_layout.addRow("Wait after ejection", self.wait_spin)

        self.show_plate_number_check = QCheckBox("Show current plate in the hundreds digit of remaining time")
        self.show_plate_number_check.setChecked(True)
        options_layout.addRow("Remaining-time plate number", self.show_plate_number_check)

        self.swap_final_check = QCheckBox("Run swap G-code after the last plate")
        self.swap_final_check.setChecked(True)
        options_layout.addRow("Final swap", self.swap_final_check)

        self.patch_check = QCheckBox("Apply editable G-code patches")
        self.patch_check.setToolTip("Uses gcode_patches.ini")
        self.patch_check.setChecked(True)
        patch_row = QHBoxLayout()
        open_patch_button = QPushButton("Open Config")
        open_patch_button.clicked.connect(self.open_patch_config)
        patch_row.addWidget(self.patch_check)
        patch_row.addWidget(open_patch_button)
        patch_row.addStretch(1)
        options_layout.addRow("G-code patches", patch_row)

        self.metadata_combo = QComboBox()
        self.metadata_combo.addItem("Keep source prediction and weight", "source")
        self.metadata_combo.addItem("Sum prediction and filament", "sum")
        self.metadata_combo.setFixedWidth(260)
        options_layout.addRow("3MF metadata", self.metadata_combo)

        self.zip_level_combo = QComboBox()
        for level in range(1, 10):
            self.zip_level_combo.addItem(f"Level {level}", level)
        self.zip_level_combo.setCurrentIndex(DEFAULT_ZIP_COMPRESS_LEVEL - 1)
        self.zip_level_combo.setFixedWidth(120)
        self.zip_level_combo.setToolTip("zlib-ng Deflate compression level for the output 3MF.")
        options_layout.addRow("ZIP compression", self.zip_level_combo)

        self.individual_batch_check = QCheckBox("Individual batch mode")
        self.individual_batch_check.setToolTip(
            "Build each input row as a separate output file, using that row's copy count."
        )
        options_layout.addRow("Batch mode", self.individual_batch_check)

        self.clear_after_build_check = QCheckBox("Clear input list after successful build")
        self.clear_after_build_check.setChecked(False)
        self.skip_duplicates_check = QCheckBox("Skip duplicate file paths when adding inputs")
        self.skip_duplicates_check.setChecked(True)
        input_handling_row = QHBoxLayout()
        input_handling_row.addWidget(self.skip_duplicates_check)
        input_handling_row.addWidget(self.clear_after_build_check)
        input_handling_row.addStretch(1)
        options_layout.addRow("Input handling", input_handling_row)
        root.addWidget(options_group)

        output_group = QGroupBox("Output")
        output_layout = QFormLayout(output_group)
        output_dir_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("Leave empty to write next to the input file")
        self.output_dir_edit.setMinimumWidth(280)
        self.output_dir_edit.setMaximumWidth(560)
        browse_output_dir_button = QPushButton("Browse")
        browse_output_dir_button.setFixedWidth(browse_output_dir_button.sizeHint().width())
        browse_output_dir_button.clicked.connect(self.choose_output_dir)
        output_dir_row.addWidget(self.output_dir_edit, 1)
        output_dir_row.addWidget(browse_output_dir_button)
        output_layout.addRow("Output directory", output_dir_row)

        self.output_name_edit = QLineEdit(DEFAULT_OUTPUT_PATTERN)
        self.output_name_edit.setPlaceholderText("Use tokens such as {source}, {sources}, {plates}, {copies}, {date}, {time}")
        self.output_name_edit.setMinimumWidth(280)
        self.output_name_edit.setMaximumWidth(560)
        filename_rule_row = QHBoxLayout()
        output_rule_help_button = QPushButton("?")
        output_rule_help_button.setFixedWidth(34)
        output_rule_help_button.setToolTip("Show filename token help")
        output_rule_help_button.clicked.connect(self.show_output_rule_help)
        filename_rule_row.addWidget(self.output_name_edit, 1)
        filename_rule_row.addWidget(output_rule_help_button)
        output_layout.addRow("Output filename rule", filename_rule_row)

        self.output_preview_label = QLabel("-")
        output_layout.addRow("Preview", self.output_preview_label)
        root.addWidget(output_group)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(100)
        root.addWidget(self.log, 0)

        self.setCentralWidget(central)
        self.success_toast = SuccessToast(central)
        self.update_vertical_growth_policy()

    def update_vertical_growth_policy(self) -> None:
        if not hasattr(self, "root_layout") or not hasattr(self, "file_group") or not hasattr(self, "log"):
            return
        shared_growth = self.height() >= 980
        if shared_growth == self._shared_growth_enabled:
            return
        self._shared_growth_enabled = shared_growth
        self.root_layout.setStretchFactor(self.file_group, 4 if shared_growth else 1)
        self.root_layout.setStretchFactor(self.log, 1 if shared_growth else 0)

    def resizeEvent(self, event: QEvent) -> None:
        super().resizeEvent(event)
        self.update_vertical_growth_policy()
        self.update_thumbnail_preview()

    def connect_option_signals(self) -> None:
        self.swap_gcode_combo.currentIndexChanged.connect(self.save_current_settings)
        self.default_copies_spin.valueChanged.connect(self.save_current_settings)
        self.bed_cooldown_check.stateChanged.connect(self.save_current_settings)
        self.cool_bed_spin.valueChanged.connect(self.save_current_settings)
        self.wait_spin.valueChanged.connect(self.save_current_settings)
        self.show_plate_number_check.stateChanged.connect(self.save_current_settings)
        self.swap_final_check.stateChanged.connect(self.save_current_settings)
        self.patch_check.stateChanged.connect(self.save_current_settings)
        self.metadata_combo.currentIndexChanged.connect(self.save_current_settings)
        self.zip_level_combo.currentIndexChanged.connect(self.save_current_settings)
        self.individual_batch_check.stateChanged.connect(self.on_individual_batch_toggled)
        self.clear_after_build_check.stateChanged.connect(self.save_current_settings)
        self.skip_duplicates_check.stateChanged.connect(self.save_current_settings)
        self.output_dir_edit.textChanged.connect(self.on_output_rule_changed)
        self.output_name_edit.textChanged.connect(self.on_output_rule_changed)

    def restore_settings_to_ui(self) -> None:
        options = self._settings.get("packing_options", {})
        if not isinstance(options, dict):
            return
        self.default_copies_spin.setValue(int(options.get("default_copies", 1)))
        self.bed_cooldown_check.setChecked(bool(options.get("bed_cooldown_enabled", True)))
        self.cool_bed_spin.setValue(int(options.get("cool_bed_temp", 45)))
        self.wait_spin.setValue(int(options.get("wait_after_eject_seconds", 45)))
        self.show_plate_number_check.setChecked(bool(options.get("show_plate_number", True)))
        self.swap_final_check.setChecked(bool(options.get("swap_after_final", True)))
        self.patch_check.setChecked(bool(options.get("apply_gcode_patches", True)))
        self.individual_batch_check.setChecked(bool(options.get("individual_batch_mode", False)))
        self.clear_after_build_check.setChecked(bool(options.get("clear_after_build", False)))
        self.skip_duplicates_check.setChecked(bool(options.get("skip_duplicates", True)))
        self.output_dir_edit.setText(str(options.get("output_directory", "")))
        self.output_name_edit.setText(str(options.get("output_filename_rule", DEFAULT_OUTPUT_PATTERN)))
        metadata_mode = options.get("metadata_mode", "source")
        metadata_index = self.metadata_combo.findData(metadata_mode)
        if metadata_index >= 0:
            self.metadata_combo.setCurrentIndex(metadata_index)
        try:
            zip_level = int(options.get("zip_compress_level", DEFAULT_ZIP_COMPRESS_LEVEL))
        except (TypeError, ValueError):
            zip_level = DEFAULT_ZIP_COMPRESS_LEVEL
        zip_level_index = self.zip_level_combo.findData(min(9, max(1, zip_level)))
        if zip_level_index >= 0:
            self.zip_level_combo.setCurrentIndex(zip_level_index)
        swap_gcode = options.get("swap_gcode") or self._settings.get("last_swap_gcode")
        if swap_gcode:
            index = self.swap_gcode_combo.findData(str(swap_gcode))
            if index < 0:
                index = self.swap_gcode_combo.findText(Path(str(swap_gcode)).name)
            if index >= 0:
                self.swap_gcode_combo.setCurrentIndex(index)

    def collect_current_settings(self) -> dict[str, Any]:
        return {
            "swap_gcode": self.swap_gcode_combo.currentData() or "",
            "default_copies": self.default_copies_spin.value(),
            "bed_cooldown_enabled": self.bed_cooldown_check.isChecked(),
            "cool_bed_temp": self.cool_bed_spin.value(),
            "wait_after_eject_seconds": self.wait_spin.value(),
            "show_plate_number": self.show_plate_number_check.isChecked(),
            "swap_after_final": self.swap_final_check.isChecked(),
            "apply_gcode_patches": self.patch_check.isChecked(),
            "metadata_mode": self.metadata_combo.currentData(),
            "zip_compress_level": int(self.zip_level_combo.currentData() or DEFAULT_ZIP_COMPRESS_LEVEL),
            "individual_batch_mode": self.individual_batch_check.isChecked(),
            "clear_after_build": self.clear_after_build_check.isChecked(),
            "skip_duplicates": self.skip_duplicates_check.isChecked(),
            "output_directory": self.output_dir_edit.text().strip(),
            "output_filename_rule": self.output_name_edit.text().strip() or DEFAULT_OUTPUT_PATTERN,
        }

    def save_current_settings(self) -> None:
        if self._loading_settings:
            return
        self._settings["packing_options"] = self.collect_current_settings()
        self._settings["last_swap_gcode"] = self.swap_gcode_combo.currentData() or ""
        self.save_settings()

    def on_output_rule_changed(self) -> None:
        self.update_output_preview()
        self.save_current_settings()

    def on_individual_batch_toggled(self, state: int) -> None:
        self.update_output_preview()
        self.save_current_settings()
        if self._loading_settings:
            return
        if state != 0:
            QMessageBox.information(
                self,
                "Individual batch mode",
                "Individual batch mode treats every input row as a separate build.\n\n"
                "Example: if you add 20 single-plate 3MF files and set copies to 5, "
                "Build 3MF will create 20 separate packed files. Each output contains "
                "only that source file repeated 5 times.\n\n"
                "This is useful for quickly batch-converting many independent 3MF jobs "
                "into multi-copy SwapMod packs. It does not combine all input files into one 3MF.",
            )

    def show_output_rule_help(self) -> None:
        QMessageBox.information(
            self,
            "Output filename rule",
            "Available filename tokens:\n\n"
            "{source}  - First input file stem, without .3mf\n"
            "{sources} - Source name summary. For multiple different inputs, it becomes "
            "first_source_and_N_more\n"
            "{plates}  - Total plate count in the output\n"
            "{copies}  - Total copy count used for the output\n"
            "{date}    - Current date as YYYYMMDD\n"
            "{time}    - Current time as HHMMSS\n\n"
            "Default rule:\n"
            "{plates} Plates - {sources}.3mf\n\n"
            "In individual batch mode, these tokens are calculated separately for each input row.",
        )

    def load_swap_gcode_to_combo(self) -> None:
        current = self.swap_gcode_combo.currentData() or self._settings.get("last_swap_gcode")
        options = self._settings.get("packing_options", {})
        if isinstance(options, dict):
            current = current or options.get("swap_gcode")
        self.swap_gcode_combo.clear()
        files = list_swap_gcode_files(default_swap_gcode_dir())
        for path in files:
            self.swap_gcode_combo.addItem(path.name, str(path))
        if current:
            index = self.swap_gcode_combo.findData(str(current))
            if index < 0:
                index = self.swap_gcode_combo.findText(Path(str(current)).name)
            if index >= 0:
                self.swap_gcode_combo.setCurrentIndex(index)
        if not files:
            self.log.append(f"No swap G-code files found in {default_swap_gcode_dir()}")

    def open_path(self, path: Path) -> None:
        target = path if path.exists() else path.parent
        target.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target)], check=False)

    def open_swap_gcode_folder(self) -> None:
        folder = default_swap_gcode_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.open_path(folder)

    def open_patch_config(self) -> None:
        path = default_patch_config_path()
        if not path.exists():
            QMessageBox.information(self, APP_NAME, f"Patch config does not exist yet:\n{path}")
            return
        self.open_path(path)

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add 3MF files", "", "3MF files (*.3mf);;All files (*)")
        self.add_paths([Path(file_name) for file_name in files])

    def create_order_widget(self, row: int) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        number_label = QLabel(str(row + 1))
        number_label.setAlignment(Qt.AlignCenter)
        number_label.setMinimumWidth(24)

        up_button = QToolButton()
        up_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        up_button.setToolTip("Move up")
        up_button.setAutoRaise(True)
        up_button.setFixedSize(28, 28)
        up_button.setEnabled(row > 0)
        up_button.clicked.connect(lambda _checked=False, current_row=row: self.move_row(current_row, -1))

        down_button = QToolButton()
        down_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        down_button.setToolTip("Move down")
        down_button.setAutoRaise(True)
        down_button.setFixedSize(28, 28)
        down_button.setEnabled(row < self.table.rowCount() - 1)
        down_button.clicked.connect(lambda _checked=False, current_row=row: self.move_row(current_row, 1))

        layout.addWidget(number_label)
        layout.addWidget(up_button)
        layout.addWidget(down_button)
        return widget

    def update_order_controls(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.setCellWidget(row, ORDER_COLUMN, self.create_order_widget(row))
            self.table.setCellWidget(row, COPIES_COLUMN, self.create_copies_spin(row, self.get_row_copies(row)))

    def create_copies_spin(self, row: int, copies: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 9999)
        spin.setFixedWidth(78)
        spin.setAlignment(Qt.AlignCenter)
        spin.setValue(max(1, int(copies)))
        spin.valueChanged.connect(lambda value, current_row=row: self.on_row_copies_changed(current_row, value))
        return spin

    def row_path(self, row: int) -> Path | None:
        item = self.table.item(row, FILE_COLUMN)
        if item is None:
            return None
        path_value = item.data(PATH_ROLE)
        if path_value:
            return Path(str(path_value))
        return Path(item.text())

    def load_thumbnail_pixmap(self, path: Path) -> QPixmap | None:
        with zipfile.ZipFile(path, "r") as archive:
            gcode_members = list_gcode_members(archive)
            active_gcode_member = resolve_output_gcode_member(archive, gcode_members[0]) if gcode_members else None
            member_name = first_preview_image_member(archive.namelist(), active_gcode_member)
            if member_name is None:
                return None
            data = archive.read(member_name)
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return None
        return pixmap

    def update_thumbnail_preview(self) -> None:
        if not hasattr(self, "thumbnail_label"):
            return
        row = self.selected_row()
        path = self.row_path(row) if row is not None else None
        self.thumbnail_label.clear()
        if path is None:
            self.thumbnail_label.setText("Select an input file")
            self.thumbnail_name_label.setText("")
            return
        self.thumbnail_name_label.setText(path.name)
        try:
            pixmap = self.load_thumbnail_pixmap(path)
        except Exception as exc:
            self.thumbnail_label.setText("Preview unavailable")
            self.thumbnail_label.setToolTip(str(exc))
            return
        if pixmap is None:
            self.thumbnail_label.setText("No preview image")
            self.thumbnail_label.setToolTip("")
            return
        scaled = pixmap.scaled(self.thumbnail_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.thumbnail_label.setPixmap(scaled)
        self.thumbnail_label.setToolTip(str(path))

    def add_paths(self, paths: list[Path]) -> None:
        expanded: list[Path] = []
        for path in paths:
            if path.is_dir():
                expanded.extend(sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() == ".3mf"))
            elif path.is_file() and path.suffix.lower() == ".3mf":
                expanded.append(path)
        if not expanded:
            return
        existing: set[str] = set()
        for row in range(self.table.rowCount()):
            current_path = self.row_path(row)
            if current_path is not None:
                existing.add(str(current_path))
        added = 0
        skipped = 0
        for path in expanded:
            normalized = str(path.resolve())
            if self.skip_duplicates_check.isChecked() and normalized in existing:
                skipped += 1
                continue
            if self.add_file_row(normalized, self.default_copies_spin.value()):
                existing.add(normalized)
                added += 1
            else:
                skipped += 1
        if added or skipped:
            self.log.append(f"Added {added} input file(s). Skipped {skipped}.")
        self.update_total_summary()
        self.update_output_preview()

    def add_file_row(self, file_name: str, copies: int) -> bool:
        path = Path(file_name)
        try:
            summary = read_3mf_summary(path)
        except Exception as exc:
            self.log.append(f"Skipped {path}: {exc}")
            return False
        row = self.table.rowCount()
        self._updating_table = True
        try:
            self.table.insertRow(row)
            path_item = QTableWidgetItem(path.name)
            path_item.setFlags(path_item.flags() & ~Qt.ItemIsEditable)
            path_item.setData(PATH_ROLE, str(path))
            path_item.setData(SUMMARY_ROLE, self.summary_to_dict(summary))
            path_item.setToolTip(f"{path}\n\n{self.base_summary_tooltip(summary)}")
            path_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            time_item = QTableWidgetItem("")
            filament_item = QTableWidgetItem("")
            for item in (time_item, filament_item):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setCellWidget(row, ORDER_COLUMN, self.create_order_widget(row))
            self.table.setItem(row, FILE_COLUMN, path_item)
            self.table.setCellWidget(row, COPIES_COLUMN, self.create_copies_spin(row, copies))
            self.table.setItem(row, TIME_COLUMN, time_item)
            self.table.setItem(row, FILAMENT_COLUMN, filament_item)
            self.table.setRowHeight(row, 38)
            self.update_row_stats(row)
        finally:
            self._updating_table = False
        self.update_order_controls()
        if self.table.rowCount() == 1:
            self.table.selectRow(0)
        return True

    def summary_to_dict(self, summary: ThreeMfSummary) -> dict[str, Any]:
        return {
            "source_3mf": str(summary.source_3mf),
            "plate_count": summary.plate_count,
            "prediction_seconds": summary.prediction_seconds,
            "weight_grams": summary.weight_grams,
            "filament_used_m": summary.filament_used_m,
            "filament_used_g": summary.filament_used_g,
        }

    def base_summary_tooltip(self, summary: ThreeMfSummary) -> str:
        return (
            f"Base plates: {summary.plate_count}\n"
            f"Base time: {format_duration(summary.prediction_seconds)}\n"
            f"Base filament: {format_filament(summary.weight_grams, summary.filament_used_m)}"
        )

    def get_row_copies(self, row: int) -> int:
        widget = self.table.cellWidget(row, COPIES_COLUMN)
        if isinstance(widget, QSpinBox):
            return max(1, int(widget.value()))
        item = self.table.item(row, COPIES_COLUMN)
        if item is None:
            return 1
        try:
            return max(1, int(item.text().strip()))
        except Exception:
            return 1

    def set_row_copies(self, row: int, copies: int) -> None:
        value = max(1, int(copies))
        widget = self.table.cellWidget(row, COPIES_COLUMN)
        if isinstance(widget, QSpinBox):
            widget.setValue(value)
            return
        item = self.table.item(row, COPIES_COLUMN)
        if item is not None:
            item.setText(str(value))

    def on_row_copies_changed(self, row: int, copies: int) -> None:
        if self._updating_table:
            return
        self._updating_table = True
        try:
            self.update_row_stats(row)
        finally:
            self._updating_table = False
        self.update_total_summary()
        self.update_output_preview()

    def update_row_stats(self, row: int) -> None:
        path_item = self.table.item(row, FILE_COLUMN)
        if path_item is None:
            return
        data = path_item.data(SUMMARY_ROLE)
        if isinstance(data, ThreeMfSummary):
            summary = data
        elif isinstance(data, dict):
            summary = three_mf_summary_from_mapping(data)
        else:
            return
        copies = self.get_row_copies(row)
        total_prediction = None if summary.prediction_seconds is None else summary.prediction_seconds * copies
        total_weight = None if summary.weight_grams is None else summary.weight_grams * copies
        total_used_m = None if summary.filament_used_m is None else summary.filament_used_m * copies
        self.table.item(row, TIME_COLUMN).setText(format_duration(total_prediction))
        self.table.item(row, FILAMENT_COLUMN).setText(format_filament(total_weight, total_used_m))

    def on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table:
            return
        if item.column() == COPIES_COLUMN:
            self._updating_table = True
            try:
                copies = self.get_row_copies(item.row())
                item.setText(str(copies))
                self.update_row_stats(item.row())
            finally:
                self._updating_table = False
        self.update_total_summary()
        self.update_output_preview()

    def selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def selected_row(self) -> int | None:
        rows = self.selected_rows()
        return rows[0] if rows else None

    def remove_selected(self) -> None:
        rows = sorted(self.selected_rows(), reverse=True)
        next_row = min(rows) if rows else None
        for row in rows:
            self.table.removeRow(row)
        self.update_order_controls()
        if next_row is not None and self.table.rowCount() > 0:
            self.table.selectRow(min(next_row, self.table.rowCount() - 1))
        self.update_total_summary()
        self.update_output_preview()
        self.update_thumbnail_preview()

    def remove_all(self) -> None:
        self.table.setRowCount(0)
        self.update_order_controls()
        self.update_total_summary()
        self.update_output_preview()
        self.update_thumbnail_preview()

    def move_selected(self, delta: int) -> None:
        row = self.selected_row()
        if row is None:
            return
        self.move_row(row, delta)

    def move_row(self, row: int, delta: int) -> None:
        new_row = row + delta
        if new_row < 0 or new_row >= self.table.rowCount():
            return
        copies = self.get_row_copies(row)
        row_items: list[QTableWidgetItem | None] = []
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            row_items.append(item.clone() if item is not None else None)
        self._updating_table = True
        try:
            self.table.removeRow(row)
            self.table.insertRow(new_row)
            for col, item in enumerate(row_items):
                if item is not None:
                    self.table.setItem(new_row, col, item)
            self.table.setCellWidget(new_row, COPIES_COLUMN, self.create_copies_spin(new_row, copies))
            self.table.setRowHeight(new_row, 38)
        finally:
            self._updating_table = False
        self.update_order_controls()
        self.table.selectRow(new_row)
        self.update_total_summary()
        self.update_output_preview()
        self.update_thumbnail_preview()

    def apply_default_copies_to_selected(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        self._updating_table = True
        try:
            for row in rows:
                self.set_row_copies(row, self.default_copies_spin.value())
                self.update_row_stats(row)
        finally:
            self._updating_table = False
        self.update_total_summary()
        self.update_output_preview()

    def choose_output_dir(self) -> None:
        start_dir = self.output_dir_edit.text().strip()
        first_path = self.row_path(0) if self.table.rowCount() > 0 else None
        if not start_dir and first_path is not None:
            start_dir = str(first_path.parent)
        directory = QFileDialog.getExistingDirectory(self, "Choose output directory", start_dir or "")
        if directory:
            self.output_dir_edit.setText(directory)

    def collect_jobs(self) -> list[PlateJob]:
        jobs: list[PlateJob] = []
        for row in range(self.table.rowCount()):
            path = self.row_path(row)
            if path is None:
                continue
            jobs.append(PlateJob(path, self.get_row_copies(row)))
        return jobs

    def summary_for_path(self, path: Path) -> ThreeMfSummary:
        normalized = str(path)
        for row in range(self.table.rowCount()):
            row_path = self.row_path(row)
            item = self.table.item(row, FILE_COLUMN)
            if row_path is not None and str(row_path) == normalized and item is not None:
                data = item.data(SUMMARY_ROLE)
                if isinstance(data, ThreeMfSummary):
                    return data
                if isinstance(data, dict):
                    return three_mf_summary_from_mapping(data)
        return read_3mf_summary(path)

    def output_naming_options(self) -> OutputNamingOptions:
        return OutputNamingOptions(
            output_directory=self.output_dir_edit.text().strip(),
            filename_rule=self.output_name_edit.text().strip() or DEFAULT_OUTPUT_PATTERN,
        )

    def current_total_summary(self) -> OutputSummary:
        return summarize_jobs_for_output(self.collect_jobs(), self.summary_for_path)

    def update_total_summary(self) -> None:
        summary = self.current_total_summary()
        self.total_summary_label.setText(
            "Total: "
            f"{summary.plate_count} plate(s) | "
            f"Time: {format_duration(summary.prediction_seconds)} | "
            f"Filament: {format_filament(summary.weight_grams, summary.filament_used_m)}"
        )

    def update_output_preview(self) -> None:
        jobs = self.collect_jobs()
        if not jobs:
            self.output_preview_label.setText("-")
            return
        try:
            if self.individual_batch_check.isChecked():
                first_path = resolve_output_path([jobs[0]], self.output_naming_options(), self.summary_for_path)
                self.output_preview_label.setText(
                    f"{first_path} | {len(jobs)} output file(s)"
                )
            else:
                output_path = resolve_output_path(jobs, self.output_naming_options(), self.summary_for_path)
                self.output_preview_label.setText(str(output_path))
        except Exception as exc:
            self.output_preview_label.setText(str(exc))

    def build_options_for_output(self, output_path: Path) -> BuildOptions:
        swap_gcode_path = self.swap_gcode_combo.currentData()
        if not swap_gcode_path:
            raise ValueError("Please put at least one swap G-code file in the swap_gcode folder and select it.")
        cool_bed_temp = self.cool_bed_spin.value() if self.bed_cooldown_check.isChecked() else None
        return BuildOptions(
            swap_gcode=Path(swap_gcode_path),
            output_3mf=output_path,
            cool_bed_temp=cool_bed_temp,
            wait_after_eject_seconds=self.wait_spin.value(),
            show_plate_number=self.show_plate_number_check.isChecked(),
            swap_after_final=self.swap_final_check.isChecked(),
            metadata_mode=self.metadata_combo.currentData(),
            apply_gcode_patches=self.patch_check.isChecked(),
            zip_compress_level=int(self.zip_level_combo.currentData() or DEFAULT_ZIP_COMPRESS_LEVEL),
        )

    def log_build_result(self, result: Any) -> None:
        self.log.append(f"Output: {result.output_3mf}")
        self.log.append(f"Plates: {result.plate_count}")
        self.log.append(f"Estimated source time: {format_duration(result.total_prediction_seconds)}")
        self.log.append(f"Estimated source filament: {format_filament(result.total_weight_grams)}")
        self.log.append(f"G-code MD5: {result.gcode_md5}")

    def show_success_toast(self, message: str) -> None:
        self.success_toast.show_message(message)

    def build_output(self) -> None:
        jobs = self.collect_jobs()
        if not jobs:
            QMessageBox.warning(self, APP_NAME, "Please add at least one 3MF file.")
            return
        try:
            self.save_current_settings()
            if self.individual_batch_check.isChecked():
                self.build_individual_outputs(jobs)
            else:
                self.build_combined_output(jobs)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            self.log.append(f"Error: {exc}")
            return

    def build_combined_output(self, jobs: list[PlateJob]) -> None:
        output_path = resolve_output_path(jobs, self.output_naming_options(), self.summary_for_path)
        options = self.build_options_for_output(output_path)
        result = build_packed_3mf(jobs, options)
        self.log_build_result(result)
        self.show_success_toast("The packed 3MF file was created.")
        if self.clear_after_build_check.isChecked():
            self.remove_all()

    def build_individual_outputs(self, jobs: list[PlateJob]) -> None:
        used_paths: set[Path] = set()
        success_count = 0
        errors: list[str] = []
        for job in jobs:
            try:
                output_path = make_unique_for_run(
                    resolve_output_path([job], self.output_naming_options(), self.summary_for_path),
                    used_paths,
                )
                options = self.build_options_for_output(output_path)
                result = build_packed_3mf([job], options)
                self.log_build_result(result)
                success_count += 1
            except Exception as exc:
                errors.append(f"{job.source_3mf.name}: {exc}")
                self.log.append(f"Error building {job.source_3mf}: {exc}")
        if errors:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Built {success_count} file(s), but {len(errors)} file(s) failed.\n\n"
                + "\n".join(errors[:8])
                + ("\n..." if len(errors) > 8 else ""),
            )
            return
        self.show_success_toast(f"Created {success_count} packed 3MF file(s).")
        if self.clear_after_build_check.isChecked():
            self.remove_all()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        urls: list[QUrl] = event.mimeData().urls()
        paths = [Path(url.toLocalFile()) for url in urls if url.toLocalFile()]
        self.add_paths(paths)
        event.acceptProposedAction()

    def closeEvent(self, event: Any) -> None:
        self.save_current_settings()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
