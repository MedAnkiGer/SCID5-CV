"""Stage 1: Self-Report GUI — PySide6 questionnaire.

Presents all screening items one at a time with Yes/No buttons,
progress bar, language selector, and back/forward navigation.

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
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from modules.exploration_engine import AudioRecorder, transcribe_audio


# ---------------------------------------------------------------------------
# Stage 1: Self-Report Questionnaire
# ---------------------------------------------------------------------------


class SelfReportGUI(QMainWindow):
    """One-question-at-a-time screening questionnaire."""

    # Emitted when the user finishes all questions
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

        # Build ordered list of screening items
        self.item_ids = sorted(questions["screening_items"].keys(), key=lambda x: int(x[1:]))
        self.current_index = 0

        # Restore progress from session
        self.responses: dict[str, bool] = dict(session.get("screening_responses", {}))
        # Convert string 'true'/'false' to bool if needed
        for k, v in self.responses.items():
            if isinstance(v, str):
                self.responses[k] = v.lower() == "true"

        # Skip to first unanswered
        while self.current_index < len(self.item_ids) and self.item_ids[self.current_index] in self.responses:
            self.current_index += 1
        if self.current_index >= len(self.item_ids):
            self.current_index = len(self.item_ids) - 1

        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        self.setWindowTitle("SCID-5-PD Self-Report Screening")
        self.setMinimumSize(700, 400)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 20, 30, 20)

        # Top bar: language selector + progress
        top_bar = QHBoxLayout()

        lang_label = QLabel("Language:")
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Deutsch (DE)", "English (EN)"])
        self.lang_combo.setCurrentIndex(0 if self.language == "de" else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        top_bar.addWidget(lang_label)
        top_bar.addWidget(self.lang_combo)
        top_bar.addStretch()

        self.progress_label = QLabel()
        top_bar.addWidget(self.progress_label)
        layout.addLayout(top_bar)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(len(self.item_ids))
        layout.addWidget(self.progress_bar)

        # Question display
        self.question_label = QLabel()
        self.question_label.setWordWrap(True)
        self.question_label.setStyleSheet("font-size: 16px; padding: 20px;")
        self.question_label.setAlignment(Qt.AlignCenter)
        self.question_label.setMinimumHeight(120)
        layout.addWidget(self.question_label)

        # Item ID label
        self.item_id_label = QLabel()
        self.item_id_label.setStyleSheet("color: gray; font-size: 10px;")
        self.item_id_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.item_id_label)

        # Answer buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.yes_btn = QPushButton("Ja / Yes")
        self.yes_btn.setMinimumSize(120, 50)
        self.yes_btn.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.yes_btn.clicked.connect(lambda: self._answer(True))
        btn_layout.addWidget(self.yes_btn)

        btn_layout.addSpacing(30)

        self.no_btn = QPushButton("Nein / No")
        self.no_btn.setMinimumSize(120, 50)
        self.no_btn.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.no_btn.clicked.connect(lambda: self._answer(False))
        btn_layout.addWidget(self.no_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # Navigation
        nav_layout = QHBoxLayout()

        self.back_btn = QPushButton("<< Back")
        self.back_btn.clicked.connect(self._go_back)
        nav_layout.addWidget(self.back_btn)

        nav_layout.addStretch()

        self.finish_btn = QPushButton("Finish")
        self.finish_btn.clicked.connect(self._finish)
        self.finish_btn.setEnabled(False)
        nav_layout.addWidget(self.finish_btn)

        layout.addLayout(nav_layout)

    def _on_language_changed(self, index):
        self.language = "de" if index == 0 else "en"
        self.session["language"] = self.language
        self._update_display()

    def _update_display(self):
        """Update the question text, progress bar, and button states."""
        total = len(self.item_ids)
        answered = len(self.responses)

        self.progress_bar.setValue(answered)
        self.progress_label.setText(f"{answered} / {total}")

        if self.current_index < total:
            item_id = self.item_ids[self.current_index]
            item = self.questions["screening_items"][item_id]
            lang_key = f"text_{self.language}"
            text = item.get(lang_key, item.get("text_en", "???"))
            self.question_label.setText(text)
            self.item_id_label.setText(f"[{item_id} — {item.get('disorder', '?')}]")

            # Highlight if already answered
            if item_id in self.responses:
                prev = self.responses[item_id]
                self.yes_btn.setStyleSheet(
                    "font-size: 14px; font-weight: bold; background-color: #4CAF50; color: white;"
                    if prev else "font-size: 14px; font-weight: bold;"
                )
                self.no_btn.setStyleSheet(
                    "font-size: 14px; font-weight: bold; background-color: #f44336; color: white;"
                    if not prev else "font-size: 14px; font-weight: bold;"
                )
            else:
                self.yes_btn.setStyleSheet("font-size: 14px; font-weight: bold;")
                self.no_btn.setStyleSheet("font-size: 14px; font-weight: bold;")

        self.back_btn.setEnabled(self.current_index > 0)
        self.finish_btn.setEnabled(answered == total)

    def _answer(self, value: bool):
        """Record answer and advance to next question."""
        item_id = self.item_ids[self.current_index]
        self.responses[item_id] = value

        # Auto-save to session
        self.session["screening_responses"] = self.responses

        # Advance
        if self.current_index < len(self.item_ids) - 1:
            self.current_index += 1
        self._update_display()

    def _go_back(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._update_display()

    def _finish(self):
        if len(self.responses) < len(self.item_ids):
            QMessageBox.warning(self, "Incomplete", "Please answer all questions before finishing.")
            return
        self.session["screening_responses"] = self.responses
        self.finished.emit(self.responses)
        self.close()


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


class ExplorationGUI(QMainWindow):
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

        self._setup_ui()
        self._update_display()

    def _setup_ui(self):
        self.setWindowTitle("SCID-5-PD Follow-up Exploration")
        self.setMinimumSize(700, 500)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
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
        self._rec_thread = RecordingThread(self.recorder)
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
        self._trans_thread = TranscriptionThread(wav_bytes, self.language)
        self._trans_thread.finished.connect(self._on_transcription_done)
        self._trans_thread.start()

    def _on_transcription_done(self, transcript: str):
        self.transcript_edit.setPlainText(transcript)
        self.status_label.setText(f"Done ({self.recorder.duration_seconds:.1f}s)")
        self.accept_btn.setEnabled(True)
        self.rerecord_btn.setEnabled(True)

    def _rerecord(self):
        self._current_audio = None
        self.transcript_edit.clear()
        self.status_label.setText("")
        self.accept_btn.setEnabled(False)
        self.rerecord_btn.setEnabled(False)

    def _accept(self):
        crit = self.criteria[self.current_index]
        transcript = self.transcript_edit.toPlainText().strip()
        self.results[crit["criterion_id"]] = transcript

        self.current_index += 1
        if self.current_index >= len(self.criteria):
            self.finished.emit(self.results)
            self.close()
        else:
            self._update_display()
