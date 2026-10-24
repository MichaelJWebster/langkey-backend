"""Speech-to-text, behind an interface so the model can move.

The learner's route depends on `SpeechToText` and nothing else. Which
implementation it gets is a configuration choice, so the model can start as a
process on the M2 Max, become a sibling container, and later move to GPU
hosting without a route changing (ADR-032).
"""

from app.speech.base import (
    Alignment,
    CandidateScore,
    CandidateScorer,
    CandidateScores,
    SpeechToText,
    Transcript,
    TranscriptionError,
    TranscriptionUnavailable,
    WordAligner,
    WordSpan,
)
from app.speech.registry import get_aligner, get_scorer, get_transcriber

__all__ = [
    "Alignment",
    "CandidateScore",
    "CandidateScorer",
    "CandidateScores",
    "SpeechToText",
    "Transcript",
    "TranscriptionError",
    "TranscriptionUnavailable",
    "WordAligner",
    "WordSpan",
    "get_aligner",
    "get_scorer",
    "get_transcriber",
]
