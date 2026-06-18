"""Automatic chord-recognition engines and section assignment helpers."""

from __future__ import annotations

import math
import os
import re
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any


class ChordRecognitionError(RuntimeError):
    """Raised when an automatic chord-recognition engine cannot run."""


PITCH_CLASSES = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}
MAJOR_SCALE_INTERVALS = {0, 2, 4, 5, 7, 9, 11}
MINOR_SCALE_INTERVALS = {0, 2, 3, 5, 7, 8, 10}
MINOR_KEYS = {"m", "min", "minor", "aeolian", "小调"}
HPCP_ROOT_NAMES_SHARP = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]
HPCP_ROOT_NAMES_FLAT = ["A", "Bb", "B", "C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab"]
PITCH_CLASS_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PITCH_CLASS_NAMES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
KRUMHANSL_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KRUMHANSL_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

CHORD_QUALITY_TEMPLATES: dict[str, tuple[str, list[tuple[int, float]]]] = {
    "major": ("", [(0, 1.35), (4, 1.0), (7, 0.95)]),
    "minor": ("m", [(0, 1.35), (3, 1.0), (7, 0.95)]),
    "dominant7": ("7", [(0, 1.25), (4, 0.95), (7, 0.9), (10, 0.7)]),
    "major7": ("maj7", [(0, 1.25), (4, 0.95), (7, 0.9), (11, 0.7)]),
    "minor7": ("m7", [(0, 1.25), (3, 0.95), (7, 0.9), (10, 0.7)]),
    "minor7b5": ("m7b5", [(0, 1.25), (3, 0.95), (6, 0.9), (10, 0.7)]),
    "dim": ("dim", [(0, 1.25), (3, 1.0), (6, 0.95)]),
    "dim7": ("dim7", [(0, 1.2), (3, 0.95), (6, 0.9), (9, 0.7)]),
    "aug": ("aug", [(0, 1.25), (4, 1.0), (8, 0.95)]),
    "sus2": ("sus2", [(0, 1.25), (2, 0.95), (7, 0.95)]),
    "sus4": ("sus4", [(0, 1.25), (5, 0.95), (7, 0.95)]),
    "six": ("6", [(0, 1.2), (4, 0.95), (7, 0.9), (9, 0.65)]),
    "minor6": ("m6", [(0, 1.2), (3, 0.95), (7, 0.9), (9, 0.65)]),
    "add9": ("add9", [(0, 1.2), (2, 0.6), (4, 0.95), (7, 0.9)]),
    "minor_add9": ("madd9", [(0, 1.2), (2, 0.6), (3, 0.95), (7, 0.9)]),
}
BASIC_CHORD_QUALITIES = ("major", "minor")


def format_timestamp(total_seconds: int | float | None) -> str | None:
    if total_seconds is None:
        return None
    rounded = max(0, int(round(float(total_seconds))))
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def parse_timestamp(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(round(float(value))))
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None

    match = re.match(r"^(\d{1,3}):(\d{2})(?:\.\d+)?$", text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        return None if seconds >= 60 else minutes * 60 + seconds

    match = re.match(r"^(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?$", text)
    if match:
        return max(0, int(round(float(match.group(1)))))

    return None


def parse_timestamp_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        return max(0.0, numeric) if math.isfinite(numeric) else None
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text:
        return None

    match = re.match(r"^(\d{1,3}):(\d{2})(?:\.(\d+))?$", text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        if seconds >= 60:
            return None
        fraction = float(f"0.{match.group(3)}") if match.group(3) else 0.0
        return minutes * 60 + seconds + fraction

    match = re.match(r"^(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?$", text)
    if match:
        return max(0.0, float(match.group(1)))

    return None


def normalize_chord_symbol(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.upper() in {"N", "NC", "NO_CHORD", "NO CHORD"}:
        return None

    text = text.replace("♯", "#").replace("♭", "b")
    text = text.replace(":maj", "")
    text = text.replace(":min", "m")
    text = text.replace(":dim", "dim")
    text = text.replace(":aug", "aug")
    text = text.replace(":sus", "sus")
    text = text.replace("min", "m")
    text = text.replace("maj", "maj")
    text = re.sub(r"^([A-Ga-g])", lambda m: m.group(1).upper(), text)
    return text


def simplify_chord_symbol(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.upper() in {"N", "NC", "N/C", "NO_CHORD", "NO CHORD"}:
        return None

    text = text.replace("♯", "#").replace("♭", "b")
    match = re.match(r"^([A-Ga-g])([#b]?)(?::?([A-Za-z0-9_+#/-]*))?", text)
    if not match:
        return normalize_chord_symbol(text)

    root = f"{match.group(1).upper()}{match.group(2)}"
    quality = (match.group(3) or "").lower()
    if quality.startswith("min") or quality == "m":
        return f"{root}m"
    return root


def normalize_arrangement_chord_symbol(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.upper() in {"N", "NC", "N/C", "NO_CHORD", "NO CHORD"}:
        return None

    text = text.replace("♯", "#").replace("♭", "b")
    text = re.sub(r"^([A-Ga-g])", lambda match: match.group(1).upper(), text)
    replacements = [
        (":maj7", "maj7"),
        (":maj", ""),
        (":min7", "m7"),
        (":min", "m"),
        (":dim", "dim"),
        (":aug", "aug"),
        (":sus4", "sus4"),
        (":sus2", "sus2"),
        (":sus", "sus4"),
        (":7", "7"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)
    text = text.replace("min", "m")
    return text


def _event_seconds(event: dict[str, Any]) -> float | None:
    value = event.get("time_seconds")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return parse_timestamp_float(event.get("time"))


def _event_end_seconds(event: dict[str, Any]) -> float | None:
    value = event.get("end_seconds")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return parse_timestamp_float(event.get("end"))


def _event_confidence(event: dict[str, Any]) -> float | None:
    value = event.get("confidence")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _merge_adjacent_duplicate_chord_event(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    previous_chord = normalize_chord_symbol(previous.get("chord"))
    current_chord = normalize_chord_symbol(current.get("chord"))
    if not previous_chord or previous_chord != current_chord:
        return False

    previous_start = _event_seconds(previous)
    previous_end = _event_end_seconds(previous)
    current_end = _event_end_seconds(current)
    current_start = _event_seconds(current)
    resolved_end = current_end if current_end is not None else current_start
    if resolved_end is not None and (previous_end is None or resolved_end > previous_end):
        previous["end_seconds"] = float(resolved_end)
        previous["end"] = format_timestamp(resolved_end)

    previous_confidence = _event_confidence(previous)
    current_confidence = _event_confidence(current)
    if current_confidence is not None and (
        previous_confidence is None or current_confidence > previous_confidence
    ):
        previous["confidence"] = current_confidence

    if previous_start is not None and previous.get("time") is None:
        previous["time"] = format_timestamp(previous_start)
    if not previous.get("raw_chord") and current.get("raw_chord"):
        previous["raw_chord"] = current.get("raw_chord")
    if not previous.get("arrangement_chord") and current.get("arrangement_chord"):
        previous["arrangement_chord"] = current.get("arrangement_chord")
    return True


def _root_from_symbol(chord: str) -> str | None:
    match = re.match(r"^([A-G])([#b]?)", chord)
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


def _quality_from_symbol(chord: str) -> str:
    match = re.match(r"^[A-G][#b]?(.*)$", chord)
    suffix = (match.group(1) if match else "").lower()
    return "minor" if suffix.startswith("m") and not suffix.startswith("maj") else "major"


def _parse_key(value: Any) -> tuple[int, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("♯", "#").replace("♭", "b")
    match = re.match(r"^([A-Ga-g])([#b]?)(?:\s*[-_/]?\s*([A-Za-z]+|小调|大调))?", text)
    if not match:
        return None
    root = f"{match.group(1).upper()}{match.group(2)}"
    root_pc = PITCH_CLASSES.get(root)
    if root_pc is None:
        return None
    mode_text = (match.group(3) or "").lower()
    mode = "minor" if mode_text in MINOR_KEYS or text.lower().endswith("m") else "major"
    return root_pc, mode


def _is_diatonic_root(chord: str, key: Any) -> bool | None:
    parsed_key = _parse_key(key)
    root = _root_from_symbol(chord)
    if parsed_key is None or root is None:
        return None
    key_pc, mode = parsed_key
    root_pc = PITCH_CLASSES.get(root)
    if root_pc is None:
        return None
    intervals = MINOR_SCALE_INTERVALS if mode == "minor" else MAJOR_SCALE_INTERVALS
    return (root_pc - key_pc) % 12 in intervals


def _prefer_flat_roots(key: Any = None) -> bool:
    text = str(key or "").strip()
    if not text:
        return False
    normalized = text.replace("♭", "b").lower()
    match = re.match(r"^([a-g])([#b]?)", normalized)
    if not match:
        return False
    root = f"{match.group(1).upper()}{match.group(2)}"
    if "b" in root:
        return True
    return root in {"F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"}


def _symbol_for_hpcp_root(root_index: int, quality: str, key: Any = None) -> str:
    roots = HPCP_ROOT_NAMES_FLAT if _prefer_flat_roots(key) else HPCP_ROOT_NAMES_SHARP
    suffix = CHORD_QUALITY_TEMPLATES[quality][0]
    return f"{roots[root_index % 12]}{suffix}"


def _fold_hpcp_to_12_bins(frame: Any) -> list[float]:
    values = [float(value) for value in frame]
    if not values:
        return []
    if len(values) == 12:
        return values
    if len(values) % 12 != 0:
        folded = [0.0] * 12
        for index, value in enumerate(values):
            folded[index % 12] += value
        return folded

    bins_per_pitch = len(values) // 12
    return [
        sum(values[index * bins_per_pitch : (index + 1) * bins_per_pitch])
        for index in range(12)
    ]


def _normalize_vector(values: list[float]) -> list[float]:
    total = sum(max(0.0, value) for value in values)
    if total <= 0:
        return [0.0 for _ in values]
    return [max(0.0, value) / total for value in values]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _chord_template_vector(root_index: int, quality: str) -> tuple[list[float], set[int]]:
    vector = [0.0] * 12
    tone_indices: set[int] = set()
    for interval, weight in CHORD_QUALITY_TEMPLATES[quality][1]:
        pitch_class = (root_index + interval) % 12
        vector[pitch_class] = max(vector[pitch_class], weight)
        tone_indices.add(pitch_class)
    return _normalize_vector(vector), tone_indices


def _score_chroma_templates(
    chroma: list[float],
    bass_chroma: list[float] | None = None,
    key: Any = None,
    top_k: int = 5,
    qualities: tuple[str, ...] = BASIC_CHORD_QUALITIES,
) -> list[dict[str, Any]]:
    normalized_chroma = _normalize_vector(chroma)
    normalized_bass = _normalize_vector(bass_chroma or [])
    candidates: list[dict[str, Any]] = []
    enabled_qualities = tuple(quality for quality in qualities if quality in CHORD_QUALITY_TEMPLATES)
    if not enabled_qualities:
        enabled_qualities = BASIC_CHORD_QUALITIES

    for root_index in range(12):
        root_bonus = normalized_bass[root_index] * 0.18 if len(normalized_bass) == 12 else 0.0
        for quality in enabled_qualities:
            template, tone_indices = _chord_template_vector(root_index, quality)
            coverage = sum(normalized_chroma[index] for index in tone_indices)
            spill = max(0.0, 1.0 - coverage)
            score = (
                0.78 * _cosine_similarity(normalized_chroma, template)
                + 0.34 * coverage
                - 0.14 * spill
                + root_bonus
            )
            chord = _symbol_for_hpcp_root(root_index, quality, key=key)
            diatonic = _is_diatonic_root(chord, key)
            if diatonic is True:
                score += 0.025
            elif diatonic is False:
                score -= 0.015

            candidates.append(
                {
                    "chord": chord,
                    "score": round(score, 6),
                    "root": _root_from_symbol(chord),
                    "quality": quality,
                    "coverage": round(coverage, 6),
                    "bass_weight": round(root_bonus, 6),
                }
            )

    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    limited = candidates[: max(1, top_k)]
    if limited:
        top_score = float(limited[0]["score"])
        second_score = float(limited[1]["score"]) if len(limited) > 1 else top_score - 0.2
        margin = max(0.0, top_score - second_score)
        confidence = max(0.05, min(0.98, 0.42 + margin * 1.8))
        limited[0]["confidence"] = round(confidence, 6)
        limited[0]["max_confidence"] = round(confidence, 6)
        for candidate in limited[1:]:
            relative = max(0.05, min(0.95, float(candidate["score"]) / max(top_score, 1e-6)))
            candidate["confidence"] = round(relative * confidence, 6)
            candidate["max_confidence"] = candidate["confidence"]
    return limited


def _profile_score(histogram: list[float], profile: list[float], root_pc: int) -> float:
    rotated = [profile[(index - root_pc) % 12] for index in range(12)]
    return _cosine_similarity(_normalize_vector(histogram), _normalize_vector(rotated))


def _estimate_key_from_chord_events(chord_events: list[dict[str, Any]]) -> dict[str, Any]:
    histogram = [0.0] * 12
    quality_counts = {"major": 0.0, "minor": 0.0}
    sorted_events = sorted(
        [event for event in chord_events if isinstance(event, dict) and _event_seconds(event) is not None],
        key=lambda event: _event_seconds(event) or 0.0,
    )

    for index, event in enumerate(sorted_events):
        chord = normalize_chord_symbol(event.get("chord"))
        if not chord:
            continue
        root = _root_from_symbol(chord)
        root_pc = PITCH_CLASSES.get(root or "")
        if root_pc is None:
            continue
        start = _event_seconds(event)
        explicit_end = _event_end_seconds(event)
        next_event = sorted_events[index + 1] if index + 1 < len(sorted_events) else None
        next_start = _event_seconds(next_event) if isinstance(next_event, dict) else None
        end = explicit_end if explicit_end is not None and start is not None and explicit_end > start else next_start
        duration = max(0.5, (end - start) if start is not None and end is not None and end > start else 1.0)
        histogram[root_pc] += duration
        quality_counts[_quality_from_symbol(chord)] += duration

    if sum(histogram) <= 0:
        return {
            "key": None,
            "mode": None,
            "confidence": "low",
            "source": "chord-events:key-profile",
        }

    candidates: list[dict[str, Any]] = []
    for root_pc in range(12):
        candidates.append(
            {
                "key": PITCH_CLASS_NAMES_FLAT[root_pc],
                "mode": "major",
                "score": _profile_score(histogram, KRUMHANSL_MAJOR_PROFILE, root_pc),
            }
        )
        candidates.append(
            {
                "key": PITCH_CLASS_NAMES_FLAT[root_pc],
                "mode": "minor",
                "score": _profile_score(histogram, KRUMHANSL_MINOR_PROFILE, root_pc),
            }
        )
    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    best = candidates[0]
    second = candidates[1] if len(candidates) > 1 else {"score": 0.0}
    margin = float(best["score"]) - float(second["score"])
    confidence = "high" if margin >= 0.08 else "medium" if margin >= 0.035 else "low"
    return {
        "key": best["key"],
        "mode": best["mode"],
        "confidence": confidence,
        "source": "chord-events:key-profile",
        "key_candidates": [
            {
                "key": candidate["key"],
                "mode": candidate["mode"],
                "score": round(float(candidate["score"]), 6),
            }
            for candidate in candidates[:5]
        ],
        "chord_quality_balance": {
            "major": round(quality_counts["major"], 3),
            "minor": round(quality_counts["minor"], 3),
        },
    }


def _normalize_display_tempo_bpm(tempo_value: float) -> tuple[int, str | None]:
    """Return a chord-sheet-friendly tempo from a beat tracker estimate.

    Beat trackers often lock to eighth-note pulses in pop/rock recordings, so
    the returned value can be the musical tempo's double-time octave.
    """
    normalized = float(tempo_value)
    normalization: str | None = None

    if 145 <= normalized <= 210 and 58 <= normalized / 2 <= 105:
        normalized /= 2
        normalization = "half_time_double_tempo"
    elif normalized > 210 and 70 <= normalized / 2 <= 135:
        normalized /= 2
        normalization = "half_time_extreme_double_tempo"

    return int(round(normalized)), normalization


def _estimate_tempo_with_librosa(audio_path: str) -> dict[str, Any]:
    os.environ.setdefault("NUMBA_CACHE_DIR", str(Path(tempfile.gettempdir()) / "chordcraft_numba_cache"))
    try:
        import librosa
        import numpy as np
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        return {
            "tempo_bpm": None,
            "source": "librosa:beat_track",
            "error": str(exc),
        }

    try:
        y, sr = librosa.load(audio_path, sr=22050, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        if isinstance(tempo, np.ndarray):
            tempo_value = float(tempo.flatten()[0]) if tempo.size else None
        else:
            tempo_value = float(tempo)
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        return {
            "tempo_bpm": None,
            "source": "librosa:beat_track",
            "error": str(exc),
        }

    if tempo_value is None or not math.isfinite(tempo_value) or tempo_value <= 0:
        return {
            "tempo_bpm": None,
            "source": "librosa:beat_track",
            "beat_count": 0,
        }
    normalized_tempo, normalization = _normalize_display_tempo_bpm(tempo_value)
    return {
        "tempo_bpm": normalized_tempo,
        "raw_tempo_bpm": int(round(tempo_value)),
        "normalization": normalization,
        "source": "librosa:beat_track",
        "beat_count": int(len(beats)) if beats is not None else 0,
    }


def estimate_song_metadata(audio_path: str, chord_events: list[dict[str, Any]]) -> dict[str, Any]:
    key_result = _estimate_key_from_chord_events(chord_events)
    tempo_result = _estimate_tempo_with_librosa(audio_path)
    return {
        "key": key_result.get("key"),
        "mode": key_result.get("mode"),
        "tempo_bpm": tempo_result.get("tempo_bpm"),
        "time_signature": None,
        "confidence": key_result.get("confidence") or "low",
        "sources": {
            "key": key_result,
            "tempo": tempo_result,
            "time_signature": {
                "source": None,
                "reason": "The pseudo-labeling/KD ACR runtime does not output meter; no reliable meter estimator is wired in.",
            },
        },
    }


def _dedupe_chords(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for event in events:
        chord = normalize_chord_symbol(event.get("chord"))
        if not chord:
            continue
        arrangement_chord = (
            normalize_arrangement_chord_symbol(event.get("arrangement_chord"))
            or normalize_arrangement_chord_symbol(event.get("raw_chord"))
            or normalize_arrangement_chord_symbol(chord)
            or chord
        )
        seconds = event.get("time_seconds")
        if not isinstance(seconds, (int, float)) or not math.isfinite(float(seconds)):
            continue
        normalized_event = {
            "time": format_timestamp(seconds),
            "time_seconds": float(seconds),
            "chord": chord,
            "display_chord": simplify_chord_symbol(event.get("display_chord") or chord) or chord,
            "raw_chord": event.get("raw_chord"),
            "arrangement_chord": arrangement_chord,
            "source": event.get("source") or "unknown",
            "confidence": event.get("confidence"),
        }
        end_seconds = event.get("end_seconds")
        if isinstance(end_seconds, (int, float)) and math.isfinite(float(end_seconds)):
            normalized_event["end"] = format_timestamp(float(end_seconds))
            normalized_event["end_seconds"] = float(end_seconds)
        if deduped and _merge_adjacent_duplicate_chord_event(deduped[-1], normalized_event):
            continue
        deduped.append(normalized_event)
    return deduped


def postprocess_chord_events(
    events: list[dict[str, Any]],
    key: Any = None,
    min_duration_seconds: float = 1.2,
    low_confidence_threshold: float = 0.18,
) -> list[dict[str, Any]]:
    normalized = _dedupe_chords(events)
    normalized.sort(key=lambda event: _event_seconds(event) if _event_seconds(event) is not None else math.inf)
    if len(normalized) <= 2:
        return normalized

    kept: list[dict[str, Any]] = []
    for index, event in enumerate(normalized):
        seconds = _event_seconds(event)
        if seconds is None:
            continue

        previous_event = kept[-1] if kept else None
        next_event = normalized[index + 1] if index + 1 < len(normalized) else None
        next_seconds = _event_seconds(next_event) if isinstance(next_event, dict) else None
        duration = next_seconds - seconds if next_seconds is not None else None
        chord = str(event.get("chord") or "")
        confidence = _event_confidence(event)
        short_event = duration is not None and duration < min_duration_seconds
        low_confidence = confidence is not None and confidence < low_confidence_threshold
        diatonic = _is_diatonic_root(chord, key)
        non_diatonic_short = short_event and diatonic is False

        if (
            previous_event
            and next_event
            and previous_event.get("chord") == next_event.get("chord")
            and previous_event.get("arrangement_chord") == next_event.get("arrangement_chord")
            and short_event
        ):
            continue
        if previous_event and (low_confidence or non_diatonic_short):
            continue
        if kept and _merge_adjacent_duplicate_chord_event(kept[-1], event):
            continue

        kept.append(dict(event))

    return kept


def choose_chords_for_intervals(
    chord_events: list[dict[str, Any]],
    intervals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sorted_events = sorted(
        [event for event in chord_events if isinstance(event, dict) and _event_seconds(event) is not None],
        key=lambda event: _event_seconds(event) or 0,
    )
    selected_events: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for interval in intervals:
        start = _event_seconds({"time_seconds": interval.get("start_seconds")})
        end = _event_seconds({"time_seconds": interval.get("end_seconds")})
        if start is None or end is None or end <= start:
            continue

        scores: dict[str, dict[str, Any]] = {}
        for index, event in enumerate(sorted_events):
            event_start = _event_seconds(event)
            if event_start is None:
                continue
            next_event = sorted_events[index + 1] if index + 1 < len(sorted_events) else None
            next_start = _event_seconds(next_event) if isinstance(next_event, dict) else end
            event_end = next_start if next_start is not None and next_start > event_start else end
            if event_end <= start or event_start >= end:
                continue

            chord = normalize_chord_symbol(event.get("chord"))
            if not chord:
                continue
            overlap = max(0.0, min(end, event_end) - max(start, event_start))
            if overlap <= 0:
                continue
            confidence = _event_confidence(event)
            weight = confidence if confidence is not None else 1.0
            score = overlap * max(weight, 0.05)
            bucket = scores.setdefault(
                chord,
                {
                    "chord": chord,
                    "score": 0.0,
                    "duration": 0.0,
                    "max_confidence": None,
                },
            )
            bucket["score"] += score
            bucket["duration"] += overlap
            if confidence is not None:
                bucket["max_confidence"] = max(bucket["max_confidence"] or 0.0, confidence)

        candidates = sorted(
            scores.values(),
            key=lambda item: (float(item["score"]), float(item["duration"])),
            reverse=True,
        )
        selected = candidates[0] if candidates else None
        decision = {
            "start": format_timestamp(start),
            "end": format_timestamp(end),
            "start_seconds": start,
            "end_seconds": end,
            "section": interval.get("section"),
            "candidates": candidates[:5],
            "selected": selected,
        }
        decisions.append(decision)
        if not selected:
            continue
        chord = str(selected["chord"])
        if selected_events and selected_events[-1].get("chord") == chord:
            continue
        selected_events.append(
            {
                "time": format_timestamp(start),
                "time_seconds": start,
                "chord": chord,
                "source": "hybrid",
                "confidence": selected.get("max_confidence"),
            }
        )

    return selected_events, decisions


def choose_dominant_chord_for_segment(
    chord_events: list[dict[str, Any]],
    duration_seconds: float,
    previous_chord: str | None = None,
) -> dict[str, Any]:
    sorted_events = sorted(
        [event for event in chord_events if isinstance(event, dict) and _event_seconds(event) is not None],
        key=lambda event: _event_seconds(event) or 0,
    )
    scores: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(sorted_events):
        start = _event_seconds(event)
        if start is None or start >= duration_seconds:
            continue
        next_event = sorted_events[index + 1] if index + 1 < len(sorted_events) else None
        next_start = _event_seconds(next_event) if isinstance(next_event, dict) else duration_seconds
        end = next_start if next_start is not None and next_start > start else duration_seconds
        chord = normalize_chord_symbol(event.get("chord"))
        if not chord:
            continue

        overlap = max(0.0, min(duration_seconds, end) - max(0.0, start))
        if overlap <= 0:
            continue
        confidence = _event_confidence(event)
        weight = confidence if confidence is not None else 1.0
        score = overlap * max(weight, 0.05)
        bucket = scores.setdefault(
            chord,
            {
                "chord": chord,
                "score": 0.0,
                "duration": 0.0,
                "max_confidence": None,
            },
        )
        bucket["score"] += score
        bucket["duration"] += overlap
        if confidence is not None:
            bucket["max_confidence"] = max(bucket["max_confidence"] or 0.0, confidence)

    candidates = sorted(
        scores.values(),
        key=lambda item: (float(item["score"]), float(item["duration"])),
        reverse=True,
    )
    selected = candidates[0] if candidates else None
    if previous_chord and len(candidates) > 1:
        selected = next((candidate for candidate in candidates if candidate.get("chord") != previous_chord), selected)

    return {
        "candidates": candidates[:5],
        "selected": selected,
    }


def _read_pcm_wav_mono(audio_path: str) -> tuple[Any, int]:
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        raise ChordRecognitionError("numpy is required for the WAV fallback chord classifier.") from exc

    try:
        with wave.open(str(audio_path), "rb") as reader:
            channels = reader.getnchannels()
            sample_rate = reader.getframerate()
            sample_width = reader.getsampwidth()
            frame_count = reader.getnframes()
            raw = reader.readframes(frame_count)
    except wave.Error as exc:
        raise ChordRecognitionError(
            "Audio segment is not a readable PCM WAV file. Install Essentia or slice to WAV first."
        ) from exc

    if sample_width == 1:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        samples = (samples - 128.0) / 128.0
    elif sample_width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ChordRecognitionError(f"Unsupported WAV sample width: {sample_width} bytes.")

    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def _classify_single_chord_segment_with_numpy_wav(
    audio_path: str,
    key: Any = None,
    top_k: int = 5,
) -> dict[str, Any]:
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        raise ChordRecognitionError("numpy is required for the WAV fallback chord classifier.") from exc

    audio, sample_rate = _read_pcm_wav_mono(audio_path)
    duration_seconds = len(audio) / float(sample_rate)
    if duration_seconds < 0.25:
        raise ChordRecognitionError("Audio segment is too short for reliable chord classification.")

    if duration_seconds > 1.0:
        trim_seconds = min(0.18, duration_seconds * 0.08)
        trim_samples = int(trim_seconds * sample_rate)
        if len(audio) - trim_samples * 2 >= int(0.35 * sample_rate):
            audio = audio[trim_samples : len(audio) - trim_samples]

    frame_size = min(4096, max(1024, 2 ** int(math.floor(math.log2(max(1024, sample_rate // 10))))))
    hop_size = max(256, frame_size // 4)
    if len(audio) < frame_size:
        audio = np.pad(audio, (0, frame_size - len(audio)))

    window = np.hanning(frame_size).astype(np.float32)
    chroma_frames: list[list[float]] = []
    frame_weights: list[float] = []
    bass_chroma = [0.0] * 12
    frequencies = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    harmonic_mask = (frequencies >= 40.0) & (frequencies <= 5000.0)
    bass_mask = (frequencies >= 40.0) & (frequencies <= 360.0)

    for offset in range(0, max(1, len(audio) - frame_size + 1), hop_size):
        frame = audio[offset : offset + frame_size]
        if len(frame) < frame_size:
            frame = np.pad(frame, (0, frame_size - len(frame)))
        spectrum = np.abs(np.fft.rfft(frame * window))
        if float(spectrum.max(initial=0.0)) <= 1e-8:
            continue

        chroma = [0.0] * 12
        selected_frequencies = frequencies[harmonic_mask]
        selected_magnitudes = spectrum[harmonic_mask]
        threshold = max(float(selected_magnitudes.max(initial=0.0)) * 0.015, 1e-8)

        for frequency, magnitude in zip(selected_frequencies, selected_magnitudes):
            magnitude = float(magnitude)
            if magnitude < threshold:
                continue
            pitch_class = int(round(12.0 * math.log2(float(frequency) / 440.0))) % 12
            chroma[pitch_class] += magnitude

        if max(chroma) <= 0:
            continue
        energy = sum(chroma)
        chroma_frames.append(_normalize_vector(chroma))
        frame_weights.append(math.sqrt(energy))

        for frequency, magnitude in zip(frequencies[bass_mask], spectrum[bass_mask]):
            magnitude = float(magnitude)
            if magnitude < threshold:
                continue
            pitch_class = int(round(12.0 * math.log2(float(frequency) / 440.0))) % 12
            bass_chroma[pitch_class] += magnitude / math.sqrt(max(float(frequency), 1.0))

    if not chroma_frames:
        raise ChordRecognitionError("No usable harmonic features were extracted from the audio segment.")

    total_weight = sum(frame_weights)
    if total_weight <= 0:
        total_weight = float(len(frame_weights))
        frame_weights = [1.0 for _ in frame_weights]

    mean_chroma = [
        sum(frame[index] * weight for frame, weight in zip(chroma_frames, frame_weights)) / total_weight
        for index in range(12)
    ]
    sorted_by_pitch = [sorted(frame[index] for frame in chroma_frames) for index in range(12)]
    midpoint = len(chroma_frames) // 2
    median_chroma = [
        values[midpoint] if len(values) % 2 == 1 else (values[midpoint - 1] + values[midpoint]) / 2.0
        for values in sorted_by_pitch
    ]
    aggregate_chroma = [
        0.65 * mean_value + 0.35 * median_value
        for mean_value, median_value in zip(mean_chroma, median_chroma)
    ]
    candidates = _score_chroma_templates(
        aggregate_chroma,
        bass_chroma=bass_chroma,
        key=key,
        top_k=top_k,
    )
    if not candidates:
        raise ChordRecognitionError("No chord candidates were produced for the audio segment.")

    return {
        "selected": dict(candidates[0]),
        "candidates": candidates,
        "duration_seconds": duration_seconds,
        "frame_count": len(chroma_frames),
        "source": "numpy:wav-chroma-template",
        "chord_vocabulary": "basic_major_minor",
        "sample_rate": sample_rate,
        "frame_size": frame_size,
        "hop_size": hop_size,
        "chroma": [round(value, 6) for value in _normalize_vector(aggregate_chroma)],
        "chroma_labeled": {
            name: round(value, 6)
            for name, value in zip(HPCP_ROOT_NAMES_SHARP, _normalize_vector(aggregate_chroma))
        },
        "bass_chroma": [round(value, 6) for value in _normalize_vector(bass_chroma)],
        "bass_chroma_labeled": {
            name: round(value, 6)
            for name, value in zip(HPCP_ROOT_NAMES_SHARP, _normalize_vector(bass_chroma))
        },
    }


def classify_single_chord_segment(
    audio_path: str,
    key: Any = None,
    top_k: int = 5,
    sample_rate: int = 44100,
) -> dict[str, Any]:
    """Classify one short audio segment that is expected to contain one chord."""
    try:
        import essentia.standard as es
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        fallback = _classify_single_chord_segment_with_numpy_wav(
            audio_path,
            key=key,
            top_k=top_k,
        )
        fallback["fallback_reason"] = f"Essentia unavailable: {exc}"
        return fallback

    path = Path(audio_path)
    if not path.exists():
        raise ChordRecognitionError(f"Audio file not found: {audio_path}")

    try:
        audio = es.MonoLoader(filename=str(path), sampleRate=sample_rate)()
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        raise ChordRecognitionError(f"Essentia could not load audio: {exc}") from exc

    duration_seconds = len(audio) / float(sample_rate)
    if duration_seconds < 0.25:
        raise ChordRecognitionError("Audio segment is too short for reliable chord classification.")

    # Skip tiny boundary regions so transients from adjacent chords do not dominate the segment summary.
    if duration_seconds > 1.0:
        trim_seconds = min(0.18, duration_seconds * 0.08)
        trim_samples = int(trim_seconds * sample_rate)
        if len(audio) - trim_samples * 2 >= int(0.35 * sample_rate):
            audio = audio[trim_samples : len(audio) - trim_samples]

    frame_size = 4096
    hop_size = 1024

    try:
        windowing = es.Windowing(type="blackmanharris62")
        spectrum = es.Spectrum()
        peaks = es.SpectralPeaks(
            orderBy="magnitude",
            magnitudeThreshold=1e-6,
            minFrequency=40,
            maxFrequency=5000,
            maxPeaks=120,
        )
        hpcp = es.HPCP(size=36, referenceFrequency=440, normalized="unitMax")
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        raise ChordRecognitionError(f"Essentia feature extractor initialization failed: {exc}") from exc

    chroma_frames: list[list[float]] = []
    frame_weights: list[float] = []
    bass_chroma = [0.0] * 12

    try:
        for frame in es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size, startFromZero=True):
            frequencies, magnitudes = peaks(spectrum(windowing(frame)))
            chroma = _fold_hpcp_to_12_bins(hpcp(frequencies, magnitudes))
            if len(chroma) != 12 or max(chroma) <= 0:
                continue

            energy = sum(float(value) for value in magnitudes)
            if energy <= 0:
                continue
            chroma_frames.append(_normalize_vector(chroma))
            frame_weights.append(math.sqrt(energy))

            for frequency, magnitude in zip(frequencies, magnitudes):
                frequency = float(frequency)
                magnitude = float(magnitude)
                if 40.0 <= frequency <= 360.0 and magnitude > 0:
                    pitch_class = int(round(12.0 * math.log2(frequency / 440.0))) % 12
                    bass_chroma[pitch_class] += magnitude / math.sqrt(max(frequency, 1.0))
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        raise ChordRecognitionError(f"Essentia chord segment feature extraction failed: {exc}") from exc

    if not chroma_frames:
        raise ChordRecognitionError("No usable harmonic features were extracted from the audio segment.")

    total_weight = sum(frame_weights)
    if total_weight <= 0:
        total_weight = float(len(frame_weights))
        frame_weights = [1.0 for _ in frame_weights]

    mean_chroma = [
        sum(frame[index] * weight for frame, weight in zip(chroma_frames, frame_weights)) / total_weight
        for index in range(12)
    ]
    sorted_by_pitch = [sorted(frame[index] for frame in chroma_frames) for index in range(12)]
    midpoint = len(chroma_frames) // 2
    median_chroma = [
        values[midpoint] if len(values) % 2 == 1 else (values[midpoint - 1] + values[midpoint]) / 2.0
        for values in sorted_by_pitch
    ]
    aggregate_chroma = [
        0.65 * mean_value + 0.35 * median_value
        for mean_value, median_value in zip(mean_chroma, median_chroma)
    ]

    candidates = _score_chroma_templates(
        aggregate_chroma,
        bass_chroma=bass_chroma,
        key=key,
        top_k=top_k,
    )
    if not candidates:
        raise ChordRecognitionError("No chord candidates were produced for the audio segment.")

    selected = dict(candidates[0])
    return {
        "selected": selected,
        "candidates": candidates,
        "duration_seconds": duration_seconds,
        "frame_count": len(chroma_frames),
        "source": "essentia:hpcp-template",
        "chord_vocabulary": "basic_major_minor",
        "chroma": [round(value, 6) for value in _normalize_vector(aggregate_chroma)],
        "chroma_labeled": {
            name: round(value, 6)
            for name, value in zip(HPCP_ROOT_NAMES_SHARP, _normalize_vector(aggregate_chroma))
        },
        "bass_chroma": [round(value, 6) for value in _normalize_vector(bass_chroma)],
        "bass_chroma_labeled": {
            name: round(value, 6)
            for name, value in zip(HPCP_ROOT_NAMES_SHARP, _normalize_vector(bass_chroma))
        },
    }


def _beats_per_bar(time_signature: Any) -> int:
    text = str(time_signature or "").strip()
    match = re.match(r"^(\d{1,2})\s*/\s*(\d{1,2})$", text)
    if not match:
        return 4
    beats = int(match.group(1))
    return beats if 1 <= beats <= 12 else 4


def _chords_per_bar_for_density(density: str) -> int | None:
    resolved = (density or "bar").strip().lower().replace("-", "_")
    if resolved in {"raw", "detailed", "beat", "beats"}:
        return None
    if resolved in {"normal", "medium", "half_bar", "halfbar"}:
        return 2
    return 1


def _pick_group_chord(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    scores: dict[str, float] = {}
    first_seen: dict[str, dict[str, Any]] = {}
    for event in events:
        chord = normalize_chord_symbol(event.get("chord"))
        if not chord:
            continue
        confidence = event.get("confidence")
        weight = float(confidence) if isinstance(confidence, (int, float)) and math.isfinite(confidence) else 1.0
        scores[chord] = scores.get(chord, 0.0) + max(weight, 0.01)
        first_seen.setdefault(chord, event)
    if not scores:
        return None

    chord = max(scores, key=scores.get)
    source = first_seen[chord]
    return {
        "time_seconds": source.get("time_seconds"),
        "chord": chord,
        "source": source.get("source") or "unknown",
        "confidence": scores[chord] / max(1, len(events)),
    }


def _compress_beat_chords(
    events: list[dict[str, Any]],
    density: str = "bar",
    time_signature: Any = None,
) -> list[dict[str, Any]]:
    chords_per_bar = _chords_per_bar_for_density(density)
    if chords_per_bar is None:
        return _dedupe_chords(events)

    beats_per_bar = _beats_per_bar(time_signature)
    beats_per_group = max(1, int(round(beats_per_bar / chords_per_bar)))
    compressed: list[dict[str, Any]] = []
    for offset in range(0, len(events), beats_per_group):
        picked = _pick_group_chord(events[offset : offset + beats_per_group])
        if picked:
            picked["source"] = f"{picked.get('source')}:compressed-{density}"
            compressed.append(picked)
    return _dedupe_chords(compressed)


def recognize_chords_with_essentia(
    audio_path: str,
    density: str = "raw",
    time_signature: Any = None,
) -> list[dict[str, Any]]:
    try:
        import essentia.standard as es
    except Exception as exc:  # pragma: no cover - depends on optional runtime package.
        raise ChordRecognitionError(
            "Essentia is not installed. Install it in the runtime environment or run with chord_engine='llm'."
        ) from exc

    path = Path(audio_path)
    if not path.exists():
        raise ChordRecognitionError(f"Audio file not found: {audio_path}")

    sample_rate = 44100
    frame_size = 4096
    hop_size = 2048

    try:
        audio = es.MonoLoader(filename=str(path), sampleRate=sample_rate)()
        rhythm = es.RhythmExtractor2013(method="multifeature")
        bpm, beats, *_ = rhythm(audio)
        if len(beats) < 2:
            step = 60.0 / float(bpm or 90.0)
            duration = len(audio) / sample_rate
            beats = [index * step for index in range(max(2, int(duration / step)))]

        windowing = es.Windowing(type="blackmanharris62")
        spectrum = es.Spectrum()
        peaks = es.SpectralPeaks(
            orderBy="magnitude",
            magnitudeThreshold=1e-5,
            minFrequency=40,
            maxFrequency=5000,
            maxPeaks=100,
        )
        hpcp = es.HPCP(size=36, referenceFrequency=440, normalized="unitMax")

        chroma_frames = []
        for frame in es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size, startFromZero=True):
            frequencies, magnitudes = peaks(spectrum(windowing(frame)))
            chroma_frames.append(hpcp(frequencies, magnitudes))

        if not chroma_frames:
            return []

        chord_detector = es.ChordsDetectionBeats(hopSize=hop_size, sampleRate=sample_rate)
        chords, strengths = chord_detector(chroma_frames, beats)
    except Exception as exc:  # pragma: no cover - exercised only with Essentia installed.
        raise ChordRecognitionError(f"Essentia chord recognition failed: {exc}") from exc

    events = []
    for index, chord in enumerate(chords):
        if index >= len(beats):
            break
        confidence = None
        if index < len(strengths):
            try:
                confidence = float(strengths[index])
            except (TypeError, ValueError):
                confidence = None
        events.append(
            {
                "time_seconds": float(beats[index]),
                "chord": chord,
                "source": "essentia",
                "confidence": confidence,
            }
        )
    return _compress_beat_chords(events, density=density, time_signature=time_signature)


def _default_plkd_acr_dir() -> Path:
    configured = os.environ.get("CHORDCRAFT_ACR_MODEL_DIR") or os.environ.get("CHORDCRAFT_PLKD_ACR_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "third_party" / "acr_model"


def _plkd_acr_checkpoint_path(model_dir: Path, model_variant: str) -> Path:
    if model_variant == "sl":
        return model_dir / "checkpoints" / "SL" / "btc_model_large_voca.pt"
    return model_dir / "checkpoints" / "btc" / "btc_combined_best.pth"


def _parse_lab_events(lab_path: Path, source: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in lab_path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        try:
            start = float(parts[0])
            end = float(parts[1])
        except ValueError:
            continue

        raw_chord = parts[2]
        chord = simplify_chord_symbol(raw_chord)
        if not chord:
            continue
        arrangement_chord = normalize_arrangement_chord_symbol(raw_chord) or chord
        event = {
            "time": format_timestamp(start),
            "time_seconds": start,
            "end": format_timestamp(end),
            "end_seconds": end,
            "chord": chord,
            "display_chord": chord,
            "raw_chord": raw_chord,
            "arrangement_chord": arrangement_chord,
            "source": source,
            "confidence": None,
        }
        events.append(event)

    return _dedupe_chords(events)


def recognize_chords_with_plkd_acr(
    audio_path: str,
    model_variant: str = "auto",
    model_dir: str | None = None,
) -> list[dict[str, Any]]:
    resolved_variant = (model_variant or "pl").strip().lower()
    if resolved_variant not in {"auto", "pl", "sl"}:
        raise ChordRecognitionError(f"Unsupported pseudo-labeling/KD ACR model variant: {model_variant}")

    path = Path(audio_path)
    if not path.exists():
        raise ChordRecognitionError(f"Audio file not found: {audio_path}")

    runtime_dir = Path(model_dir) if model_dir else _default_plkd_acr_dir()
    if not runtime_dir.exists():
        raise ChordRecognitionError(
            f"Pseudo-labeling/KD ACR model directory not found: {runtime_dir}. "
            "Set CHORDCRAFT_ACR_MODEL_DIR to the method runtime/model directory."
        )

    if resolved_variant == "auto":
        pl_checkpoint = _plkd_acr_checkpoint_path(runtime_dir, "pl")
        sl_checkpoint = _plkd_acr_checkpoint_path(runtime_dir, "sl")
        resolved_variant = "pl" if pl_checkpoint.exists() else "sl"

    checkpoint_path = _plkd_acr_checkpoint_path(runtime_dir, resolved_variant)
    if not checkpoint_path.exists():
        raise ChordRecognitionError(
            f"Pseudo-labeling/KD BTC-{resolved_variant.upper()} checkpoint not found: {checkpoint_path}. "
            "Place the external checkpoint in the configured ACR runtime directory before running."
        )

    config_path = runtime_dir / "config" / "btc_config.yaml"
    if not config_path.exists():
        raise ChordRecognitionError(f"Pseudo-labeling/KD BTC config not found: {config_path}")

    os.environ.setdefault("NUMBA_CACHE_DIR", str(Path(tempfile.gettempdir()) / "chordcraft_numba_cache"))

    try:
        import librosa  # noqa: F401
        import mir_eval  # noqa: F401
        import torch  # noqa: F401
        import yaml  # noqa: F401
    except Exception as exc:
        raise ChordRecognitionError(
            "Pseudo-labeling/KD BTC dependencies are missing. Install at least librosa, PyYAML, mir_eval, torch, scipy, soundfile."
        ) from exc

    inserted = False
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
        inserted = True

    try:
        from btc_chord_recognition import btc_chord_recognition  # type: ignore
    except Exception as exc:
        raise ChordRecognitionError(f"Failed to import pseudo-labeling/KD BTC inference code: {exc}") from exc

    with tempfile.NamedTemporaryFile(delete=False, suffix=".lab") as temp_file:
        lab_path = Path(temp_file.name)

    try:
        ok = btc_chord_recognition(str(path), str(lab_path), model_variant=resolved_variant)
        if not ok:
            raise ChordRecognitionError(f"Pseudo-labeling/KD BTC-{resolved_variant.upper()} inference returned False.")
        events = _parse_lab_events(lab_path, source=f"plkd-btc-{resolved_variant}")
    finally:
        try:
            lab_path.unlink(missing_ok=True)
        except Exception:
            pass
        if inserted:
            try:
                sys.path.remove(str(runtime_dir))
            except ValueError:
                pass

    return events


def recognize_chords(
    audio_path: str,
    engine: str = "essentia",
    density: str = "raw",
    time_signature: Any = None,
) -> list[dict[str, Any]]:
    resolved = (engine or "essentia").strip().lower()
    if resolved == "essentia":
        return recognize_chords_with_essentia(
            audio_path,
            density=density,
            time_signature=time_signature,
        )
    if resolved in {"plkd-btc", "pseudo-label-kd-btc", "btc-kd"}:
        return recognize_chords_with_plkd_acr(audio_path, model_variant="auto")
    if resolved in {"plkd-btc-pl", "btc-pl"}:
        return recognize_chords_with_plkd_acr(audio_path, model_variant="pl")
    if resolved in {"plkd-btc-sl", "btc-sl"}:
        return recognize_chords_with_plkd_acr(audio_path, model_variant="sl")
    raise ChordRecognitionError(f"Unsupported chord recognition engine: {engine}")


def _section_start_seconds(section: dict[str, Any]) -> float | None:
    value = section.get("start_seconds")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return parse_timestamp_float(section.get("start"))


def _section_end_seconds(section: dict[str, Any]) -> float | None:
    value = section.get("end_seconds")
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return parse_timestamp_float(section.get("end"))


def _set_section_boundary(section: dict[str, Any], key: str, seconds: float) -> None:
    bounded = max(0.0, float(seconds))
    section[key] = format_timestamp(bounded)
    section[f"{key}_seconds"] = bounded


def _chord_boundary_points(chord_events: list[dict[str, Any]]) -> list[float]:
    points: set[float] = set()
    for event in chord_events:
        if not isinstance(event, dict):
            continue
        for value in [_event_seconds(event), _event_end_seconds(event)]:
            if value is not None and math.isfinite(value):
                points.add(round(max(0.0, value), 3))
    return sorted(points)


def snap_sections_to_chord_boundaries(
    sections: list[dict[str, Any]],
    chord_events: list[dict[str, Any]],
    max_snap_seconds: float = 1.0,
    min_section_seconds: float = 2.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Snap adjacent LLM section boundaries to nearby chord-event boundaries."""
    snapped = [dict(section) for section in sections]
    if len(snapped) < 2:
        return snapped, []

    chord_points = _chord_boundary_points(chord_events)
    if not chord_points:
        return snapped, []

    adjustments: list[dict[str, Any]] = []
    tolerance = max(0.0, float(max_snap_seconds))
    min_duration = max(0.0, float(min_section_seconds))

    for index in range(1, len(snapped)):
        previous = snapped[index - 1]
        current = snapped[index]
        boundary = _section_start_seconds(current)
        if boundary is None:
            boundary = _section_end_seconds(previous)
        if boundary is None:
            continue

        candidate = min(chord_points, key=lambda point: abs(point - boundary))
        distance = abs(candidate - boundary)
        if distance > tolerance:
            continue

        previous_start = _section_start_seconds(previous)
        current_end = _section_end_seconds(current)
        if previous_start is not None and candidate - previous_start < min_duration:
            continue
        if current_end is not None and current_end - candidate < min_duration:
            continue

        old_previous_end = _section_end_seconds(previous)
        old_current_start = _section_start_seconds(current)
        _set_section_boundary(previous, "end", candidate)
        _set_section_boundary(current, "start", candidate)
        adjustments.append(
            {
                "boundary_index": index,
                "previous_section": previous.get("name"),
                "current_section": current.get("name"),
                "old_previous_end_seconds": old_previous_end,
                "old_current_start_seconds": old_current_start,
                "snapped_seconds": candidate,
                "distance_seconds": round(distance, 3),
            }
        )

    return snapped, adjustments


def assign_chords_to_sections(
    chord_events: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    min_overlap_seconds: float = 0.05,
    min_overlap_ratio: float = 0.15,
) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    sorted_events = sorted(
        [event for event in chord_events if isinstance(event, dict)],
        key=lambda event: _event_seconds(event) if _event_seconds(event) is not None else math.inf,
    )
    starts = [_section_start_seconds(section) for section in sections]
    ends = [_section_end_seconds(section) for section in sections]

    event_intervals: list[dict[str, Any]] = []
    for index, event in enumerate(sorted_events):
        start = _event_seconds(event)
        if start is None:
            continue
        explicit_end = _event_end_seconds(event)
        next_event = sorted_events[index + 1] if index + 1 < len(sorted_events) else None
        next_start = _event_seconds(next_event) if isinstance(next_event, dict) else None
        end = explicit_end if explicit_end is not None and explicit_end > start else next_start
        if end is None or end <= start:
            end = start + 0.5
        event_intervals.append(
            {
                "event": event,
                "start": start,
                "end": end,
                "duration": max(0.001, end - start),
            }
        )

    for index, section in enumerate(sections):
        item = dict(section)
        start = starts[index] if starts[index] is not None else 0.0
        end = ends[index]
        next_start = starts[index + 1] if index + 1 < len(starts) else None
        if end is None:
            end = next_start

        section_chords: list[dict[str, Any]] = []
        if end is None or end <= start:
            item["chords"] = section_chords
            assigned.append(item)
            continue

        for interval in event_intervals:
            event = interval["event"]
            event_start = float(interval["start"])
            event_end = float(interval["end"])
            overlap = max(0.0, min(end, event_end) - max(start, event_start))
            if overlap < min_overlap_seconds:
                continue
            chord = normalize_chord_symbol(event.get("chord"))
            if not chord:
                continue
            if overlap / float(interval["duration"]) < min_overlap_ratio:
                continue

            display_start = max(start, event_start)
            display_end = min(end, event_end)
            chord_item = {
                "time": format_timestamp(display_start),
                "time_seconds": display_start,
                "chord": chord,
                "display_chord": simplify_chord_symbol(event.get("display_chord") or chord) or chord,
                "raw_chord": event.get("raw_chord"),
                "arrangement_chord": (
                    normalize_arrangement_chord_symbol(event.get("arrangement_chord"))
                    or normalize_arrangement_chord_symbol(event.get("raw_chord"))
                    or normalize_arrangement_chord_symbol(chord)
                    or chord
                ),
            }
            if display_end > display_start:
                chord_item["end"] = format_timestamp(display_end)
                chord_item["end_seconds"] = display_end
            if event_start < start:
                chord_item["starts_before_section"] = True
            if event_end > end:
                chord_item["continues_after_section"] = True
            chord_item["overlap_seconds"] = round(overlap, 3)
            if section_chords and _merge_adjacent_duplicate_chord_event(section_chords[-1], chord_item):
                previous_overlap = section_chords[-1].get("overlap_seconds")
                if isinstance(previous_overlap, (int, float)):
                    section_chords[-1]["overlap_seconds"] = round(float(previous_overlap) + overlap, 3)
                continue
            section_chords.append(chord_item)

        item["chords"] = section_chords
        assigned.append(item)

    return assigned
