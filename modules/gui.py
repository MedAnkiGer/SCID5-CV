"""Stage 1: Self-Report GUI — PySide6 questionnaire.

Presents all screening items grouped by disorder block (one block per page),
with a scrollable list of Yes/No rows, overall progress bar, language selector,
and block-by-block navigation.

Stage 2 Exploration GUI is also here: shows follow-up questions with
recording controls and transcript review.
"""

import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from modules.exploration_engine import AudioRecorder, transcribe_audio


# ---------------------------------------------------------------------------
# Shared: Single persistent window that hosts all GUI phases
# ---------------------------------------------------------------------------


class PipelineWindow(QMainWindow):
    """Main application window — stays open across all three GUI phases."""

    def show_widget(self, widget: QWidget) -> None:
        """Swap the central widget, cleaning up threads on the old one first."""
        old = self.centralWidget()
        if old is not None and hasattr(old, "_stop_threads"):
            old._stop_threads()
        self.setCentralWidget(widget)

    def closeEvent(self, event):
        """Clean up threads in the active widget before closing."""
        w = self.centralWidget()
        if w is not None and hasattr(w, "_stop_threads"):
            w._stop_threads()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Stage 1: Self-Report Questionnaire
# ---------------------------------------------------------------------------


class SelfReportGUI(QWidget):
    """Block-by-block screening questionnaire — all questions of one disorder on one page."""

    # ---- colour palette ----
    _C_BG        = "#F5F7F7"   # window / widget background
    _C_ROW_ODD   = "#FFFFFF"   # question row, odd
    _C_ROW_EVEN  = "#EAF4F4"   # question row, even (light teal)
    _C_TEAL      = "#00897B"   # progress bar fill, accents
    _C_TEAL_DARK = "#00695C"   # nav buttons
    _C_TEAL_MID  = "#B2DFDB"   # disabled button tint
    _C_TEXT      = "#212121"   # primary text

    # yes/no button styles — default (subtle hint) and selected (saturated)
    _S_YES_DEFAULT  = ("font-size: 12px; background-color: #E8F5E9; color: #2E7D32;"
                       " border: 1px solid #A5D6A7; border-radius: 4px;")
    _S_YES_SELECTED = ("font-size: 12px; font-weight: bold; background-color: #43A047;"
                       " color: white; border: none; border-radius: 4px;")
    _S_NO_DEFAULT   = ("font-size: 12px; background-color: #FFEBEE; color: #C62828;"
                       " border: 1px solid #EF9A9A; border-radius: 4px;")
    _S_NO_SELECTED  = ("font-size: 12px; font-weight: bold; background-color: #E53935;"
                       " color: white; border: none; border-radius: 4px;")

    _S_NAV_BTN = """
        QPushButton {{
            background-color: {bg}; color: white;
            border: none; border-radius: 4px;
            font-size: 13px; font-weight: bold;
            padding: 6px 18px;
        }}
        QPushButton:hover  {{ background-color: #00796B; }}
        QPushButton:disabled {{ background-color: #B2DFDB; color: #FFFFFF; }}
    """

    finished = Signal(dict)  # {item_id: bool, ...}

    def __init__(self, questions: dict, session: dict, parent=None):
        """
        Args:
            questions: The full questions.json data.
            session: The current session state dict (mutable, updated in-place).
        """
        super().__init__(parent)
        self.questions = questions
        self.session = session
        self.language = session.get("language", "de")

        # Ordered list of all screening item IDs
        self.item_ids = sorted(questions["screening_items"].keys(), key=lambda x: int(x[1:]))

        # Restore progress from session
        self.responses: dict[str, bool] = dict(session.get("screening_responses", {}))
        for k, v in self.responses.items():
            if isinstance(v, str):
                self.responses[k] = v.lower() == "true"

        # Build blocks: list of (disorder_key, [item_ids]) in disorders dict order
        self.blocks: list[tuple[str, list[str]]] = []
        for disorder_key in questions["disorders"]:
            block_items = [
                iid for iid in self.item_ids
                if questions["screening_items"][iid].get("disorder") == disorder_key
            ]
            if block_items:
                self.blocks.append((disorder_key, block_items))

        # Start on the first block that still has unanswered questions
        self.current_block_index = 0
        for i, (_, block_items) in enumerate(self.blocks):
            if any(iid not in self.responses for iid in block_items):
                self.current_block_index = i
                break

        # Per-row button references: item_id -> (yes_btn, no_btn)
        self._row_buttons: dict[str, tuple[QPushButton, QPushButton]] = {}

        self._setup_ui()
        self._build_block_page()

    def _setup_ui(self):
        self.setMinimumSize(750, 560)
        self.setStyleSheet(f"background-color: {self._C_BG}; color: {self._C_TEXT};")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(30, 20, 30, 20)
        self.main_layout = layout

        # Top bar: language selector + block indicator
        top_bar = QHBoxLayout()

        lang_label = QLabel("Language:")
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Deutsch (DE)", "English (EN)"])
        self.lang_combo.setCurrentIndex(0 if self.language == "de" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        top_bar.addWidget(lang_label)
        top_bar.addWidget(self.lang_combo)
        top_bar.addStretch()

        self.block_label = QLabel()
        self.block_label.setStyleSheet(f"font-weight: bold; color: {self._C_TEAL_DARK};")
        top_bar.addWidget(self.block_label)
        layout.addLayout(top_bar)

        # Overall progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(len(self.item_ids))
        self.progress_bar.setFormat("%v / %m")
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: none; border-radius: 4px;
                background-color: {self._C_TEAL_MID};
                text-align: center; color: {self._C_TEXT};
                height: 16px;
            }}
            QProgressBar::chunk {{
                background-color: {self._C_TEAL}; border-radius: 4px;
            }}
        """)
        layout.addWidget(self.progress_bar)

        # Scroll area containing the question rows
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.scroll_area)

        self.scroll_widget = QWidget()
        self.scroll_widget.setStyleSheet(f"background-color: {self._C_BG};")
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setSpacing(0)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setWidget(self.scroll_widget)

        # Navigation
        nav_layout = QHBoxLayout()

        _nav = self._S_NAV_BTN.format(bg=self._C_TEAL_DARK)
        self.back_btn = QPushButton("<< Back")
        self.back_btn.setStyleSheet(_nav)
        self.back_btn.clicked.connect(self._prev_block)
        nav_layout.addWidget(self.back_btn)

        nav_layout.addStretch()

        self.next_btn = QPushButton("Next Block >>")
        self.next_btn.setStyleSheet(_nav)
        self.next_btn.clicked.connect(self._next_block)
        nav_layout.addWidget(self.next_btn)

        self.finish_btn = QPushButton("Finish")
        self.finish_btn.setStyleSheet(_nav)
        self.finish_btn.clicked.connect(self._finish)
        nav_layout.addWidget(self.finish_btn)

        layout.addLayout(nav_layout)

    def _build_block_page(self):
        """Clear the scroll area and rebuild question rows for the current block."""
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._row_buttons.clear()

        _disorder_key, item_ids = self.blocks[self.current_block_index]
        lang = self.language

        # Update header label
        self.block_label.setText(f"Block {self.current_block_index + 1} / {len(self.blocks)}")

        # Build one row per question
        for q_num, item_id in enumerate(item_ids, 1):
            item = self.questions["screening_items"][item_id]
            text = item.get(f"text_{lang}", item.get("text_en", "???"))

            row = QWidget()
            bg = self._C_ROW_EVEN if q_num % 2 == 0 else self._C_ROW_ODD
            row.setStyleSheet(f"background-color: {bg};")

            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 8, 10, 8)
            row_layout.setSpacing(12)

            q_label = QLabel(f"{q_num}.  {text}")
            q_label.setWordWrap(True)
            q_label.setStyleSheet(
                f"font-size: 13px; background-color: {bg}; color: {self._C_TEXT};"
            )
            row_layout.addWidget(q_label, stretch=1)

            yes_btn = QPushButton("Ja" if lang == "de" else "Yes")
            yes_btn.setFixedSize(90, 34)
            yes_btn.setStyleSheet(self._S_YES_DEFAULT)
            yes_btn.clicked.connect(
                lambda checked=False, iid=item_id: self._row_answer(iid, True)
            )
            row_layout.addWidget(yes_btn, 0, Qt.AlignTop)

            no_btn = QPushButton("Nein" if lang == "de" else "No")
            no_btn.setFixedSize(90, 34)
            no_btn.setStyleSheet(self._S_NO_DEFAULT)
            no_btn.clicked.connect(
                lambda checked=False, iid=item_id: self._row_answer(iid, False)
            )
            row_layout.addWidget(no_btn, 0, Qt.AlignTop)

            self._row_buttons[item_id] = (yes_btn, no_btn)
            self.scroll_layout.addWidget(row)

            if item_id in self.responses:
                self._apply_row_style(item_id, self.responses[item_id])

        self.scroll_layout.addStretch()
        self._update_nav()
        self._update_progress()

    def _apply_row_style(self, item_id: str, value: bool):
        yes_btn, no_btn = self._row_buttons[item_id]
        if value:
            yes_btn.setStyleSheet(self._S_YES_SELECTED)
            no_btn.setStyleSheet(self._S_NO_DEFAULT)
        else:
            yes_btn.setStyleSheet(self._S_YES_DEFAULT)
            no_btn.setStyleSheet(self._S_NO_SELECTED)

    def _row_answer(self, item_id: str, value: bool):
        self.responses[item_id] = value
        self.session["screening_responses"] = self.responses
        self._apply_row_style(item_id, value)
        self._update_nav()
        self._update_progress()

    def _update_progress(self):
        self.progress_bar.setValue(len(self.responses))

    def _update_nav(self):
        _, item_ids = self.blocks[self.current_block_index]
        all_answered = all(iid in self.responses for iid in item_ids)
        is_last = self.current_block_index == len(self.blocks) - 1

        self.back_btn.setEnabled(self.current_block_index > 0)
        self.next_btn.setVisible(not is_last)
        self.next_btn.setEnabled(all_answered)
        self.finish_btn.setVisible(is_last)
        self.finish_btn.setEnabled(len(self.responses) == len(self.item_ids))

    def _prev_block(self):
        if self.current_block_index > 0:
            self.current_block_index -= 1
            self._build_block_page()

    def _next_block(self):
        _, item_ids = self.blocks[self.current_block_index]
        if not all(iid in self.responses for iid in item_ids):
            QMessageBox.warning(
                self, "Incomplete",
                "Please answer all questions in this block before continuing."
            )
            return
        self.current_block_index += 1
        self._build_block_page()

    def _on_language_changed(self, index):
        self.language = "de" if index == 0 else "en"
        self.session["language"] = self.language
        self._build_block_page()

    def _finish(self):
        if len(self.responses) < len(self.item_ids):
            QMessageBox.warning(self, "Incomplete", "Please answer all questions before finishing.")
            return
        self.session["screening_responses"] = self.responses
        self.finished.emit(self.responses)


# ---------------------------------------------------------------------------
# Stage 2: Exploration GUI — Recording & Transcript Review
# ---------------------------------------------------------------------------


class RecordingThread(QThread):
    """Background thread for audio recording (non-blocking GUI)."""

    finished = Signal(object)  # emits numpy array of audio data

    def __init__(self, recorder: AudioRecorder, parent=None):
        super().__init__(parent)
        self.recorder = recorder

    def run(self):
        audio_data = self.recorder.record_blocking()
        self.finished.emit(audio_data)


class TranscriptionThread(QThread):
    """Background thread for Whisper API call."""

    finished = Signal(str)  # emits transcript text

    def __init__(self, wav_bytes: bytes, language: str, parent=None):
        super().__init__(parent)
        self.wav_bytes = wav_bytes
        self.language = language

    def run(self):
        try:
            transcript = transcribe_audio(self.wav_bytes, self.language)
        except Exception as e:
            transcript = f"[Transcription error: {e}]"
        self.finished.emit(transcript)


class ExplorationGUI(QWidget):
    """Follow-up question interview with audio recording."""

    # Emitted when all explorations are done: {criterion_id: transcript}
    finished = Signal(dict)

    def __init__(self, criteria_to_explore: list[dict], language: str = "de", parent=None):
        """
        Args:
            criteria_to_explore: List of dicts, each with keys:
                criterion_id, followup_question_de, followup_question_en,
                description_de, description_en,
                clarifying_question (optional, str or None).
            language: 'de' or 'en'.
        """
        super().__init__(parent)
        self.criteria = criteria_to_explore
        self.language = language
        self.current_index = 0
        self.results: dict[str, str] = {}  # criterion_id -> transcript
        self.recorder = AudioRecorder()
        self._current_audio = None
        self._rec_thread = None
        self._trans_thread = None

        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(30, 20, 30, 20)

        # Progress
        self.progress_label = QLabel()
        self.progress_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(len(self.criteria))
        layout.addWidget(self.progress_bar)

        # Criterion description
        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("font-size: 11px; color: #555; padding: 5px;")
        layout.addWidget(self.desc_label)

        # Follow-up question
        self.question_label = QLabel()
        self.question_label.setWordWrap(True)
        self.question_label.setStyleSheet("font-size: 14px; padding: 10px; font-weight: bold;")
        self.question_label.setMinimumHeight(80)
        layout.addWidget(self.question_label)

        # Recording controls
        rec_layout = QHBoxLayout()
        rec_layout.addStretch()

        self.record_btn = QPushButton("Start Recording")
        self.record_btn.setMinimumSize(160, 45)
        self.record_btn.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.record_btn.clicked.connect(self._toggle_recording)
        rec_layout.addWidget(self.record_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; color: red;")
        rec_layout.addWidget(self.status_label)

        rec_layout.addStretch()
        layout.addLayout(rec_layout)

        # Transcript display
        self.transcript_edit = QTextEdit()
        self.transcript_edit.setPlaceholderText("Transcript will appear here after recording...")
        self.transcript_edit.setReadOnly(True)
        layout.addWidget(self.transcript_edit)

        # Accept / Re-record buttons
        action_layout = QHBoxLayout()

        self.rerecord_btn = QPushButton("Re-record")
        self.rerecord_btn.clicked.connect(self._rerecord)
        self.rerecord_btn.setEnabled(False)
        action_layout.addWidget(self.rerecord_btn)

        action_layout.addStretch()

        self.accept_btn = QPushButton("Accept & Next")
        self.accept_btn.setStyleSheet("font-size: 13px; font-weight: bold;")
        self.accept_btn.clicked.connect(self._accept)
        self.accept_btn.setEnabled(False)
        action_layout.addWidget(self.accept_btn)

        layout.addLayout(action_layout)

    def _update_display(self):
        idx = self.current_index
        total = len(self.criteria)
        self.progress_bar.setValue(len(self.results))
        self.progress_label.setText(f"Criterion {idx + 1} / {total}")

        if idx < total:
            crit = self.criteria[idx]
            lang_suffix = f"_{self.language}"

            desc = crit.get(f"description{lang_suffix}", crit.get("description_en", ""))
            self.desc_label.setText(f"Criterion: {crit['criterion_id']} — {desc}")

            # Use clarifying question if provided, otherwise use standard follow-up
            clarifying = crit.get("clarifying_question")
            if clarifying:
                question = f"[Clarification] {clarifying}"
            else:
                question = crit.get(f"followup_question{lang_suffix}", crit.get("followup_question_en", ""))
            self.question_label.setText(question)

            self.transcript_edit.clear()
            self.record_btn.setEnabled(True)
            self.accept_btn.setEnabled(False)
            self.rerecord_btn.setEnabled(False)
            self.status_label.setText("")

    def _toggle_recording(self):
        if self.recorder.is_recording:
            self.recorder.stop_recording()
            self.record_btn.setText("Start Recording")
            self.status_label.setText("Processing...")
        else:
            self._start_recording()

    def _start_recording(self):
        self.record_btn.setText("Stop Recording")
        self.status_label.setText("Recording...")
        self.transcript_edit.clear()
        self.accept_btn.setEnabled(False)
        self.rerecord_btn.setEnabled(False)

        self.recorder = AudioRecorder()
        self._rec_thread = RecordingThread(self.recorder, parent=self)
        self._rec_thread.finished.connect(self._on_recording_done)
        self._rec_thread.start()

    def _on_recording_done(self, audio_data):
        self.record_btn.setText("Start Recording")

        if len(audio_data) == 0:
            self.status_label.setText("No audio captured.")
            self.rerecord_btn.setEnabled(True)
            return

        self._current_audio = audio_data
        self.status_label.setText("Transcribing...")

        wav_bytes = self.recorder.get_wav_bytes(audio_data)
        self._trans_thread = TranscriptionThread(wav_bytes, self.language, parent=self)
        self._trans_thread.finished.connect(self._on_transcription_done)
        self._trans_thread.start()

    def _on_transcription_done(self, transcript: str):
        self.transcript_edit.setPlainText(transcript)
        self.status_label.setText(f"Done ({self.recorder.duration_seconds:.1f}s)")
        self.accept_btn.setEnabled(True)
        self.rerecord_btn.setEnabled(True)

    def _rerecord(self):
        self._stop_threads()
        self._current_audio = None
        self.transcript_edit.clear()
        self.status_label.setText("")
        self.accept_btn.setEnabled(False)
        self.rerecord_btn.setEnabled(False)

    def _stop_threads(self):
        """Ensure all background threads are stopped before proceeding."""
        # Stop any active recording
        if self.recorder.is_recording:
            self.recorder.stop_recording()
        # Wait for recording thread to finish
        if self._rec_thread is not None and self._rec_thread.isRunning():
            self._rec_thread.wait(5000)
        # Wait for transcription thread to finish
        if self._trans_thread is not None and self._trans_thread.isRunning():
            self._trans_thread.wait(10000)

    def _accept(self):
        crit = self.criteria[self.current_index]
        transcript = self.transcript_edit.toPlainText().strip()
        self.results[crit["criterion_id"]] = transcript

        self.current_index += 1
        if self.current_index >= len(self.criteria):
            self.finished.emit(self.results)
        else:
            self._update_display()

    def load_criteria(self, criteria: list[dict]) -> None:
        """Load new criteria into the GUI without closing the window.

        Used to transition from exploration to clarification seamlessly.
        """
        self._stop_threads()
        self.criteria = criteria
        self.current_index = 0
        self.results = {}
        self._current_audio = None
        self.progress_bar.setMaximum(len(criteria))
        self._update_display()

