"""Shared transcription/diarization logic used by both the CLI and the GUI."""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

VALID_EXTENSIONS = {".mp3", ".wav", ".m4a"}
KNOWN_NAMES_PATH = Path(__file__).parent / "known_names.json"


class TranscriptionError(Exception):
    """Raised for any user-facing failure in the transcription pipeline."""


def validate_file(path_str):
    path = Path(path_str)
    if not path.exists():
        raise TranscriptionError(f"Datei nicht gefunden: {path}")
    if not path.is_file():
        raise TranscriptionError(f"Kein gültiger Dateipfad: {path}")
    if path.suffix.lower() not in VALID_EXTENSIONS:
        raise TranscriptionError(
            f"Nicht unterstütztes Format '{path.suffix}'. "
            f"Unterstützt: {', '.join(sorted(VALID_EXTENSIONS))}"
        )
    return path


def load_api_key():
    load_dotenv()
    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise TranscriptionError(
            "ASSEMBLYAI_API_KEY nicht gefunden. Kopiere .env.template zu .env "
            "und trage deinen AssemblyAI API-Key ein."
        )
    return api_key


def cache_path_for(audio_path):
    return audio_path.with_name(f"{audio_path.stem}_transcript_cache.json")


def names_path_for(audio_path):
    return audio_path.with_name(f"{audio_path.stem}_speaker_names.json")


def save_cache(cache_path, utterances):
    data = [
        {"speaker": u.speaker, "text": u.text, "start": u.start, "end": u.end}
        for u in utterances
    ]
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cache(cache_path):
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    return [
        SimpleNamespace(
            speaker=item["speaker"],
            text=item["text"],
            start=item["start"],
            end=item.get("end", item["start"]),
        )
        for item in data
    ]


def transcribe_via_api(audio_path, speakers_expected=None, status_callback=None):
    """Blocking call. status_callback(status_str) is invoked periodically while polling."""
    import assemblyai as aai

    aai.settings.api_key = load_api_key()

    config_kwargs = {"speaker_labels": True}
    if speakers_expected:
        config_kwargs["speakers_expected"] = speakers_expected
    config = aai.TranscriptionConfig(**config_kwargs)
    transcriber = aai.Transcriber(config=config)

    try:
        transcript = transcriber.submit(str(audio_path))
    except Exception as exc:
        raise TranscriptionError(f"Fehler beim Hochladen an AssemblyAI: {exc}") from exc

    try:
        while transcript.status not in (aai.TranscriptStatus.completed, aai.TranscriptStatus.error):
            time.sleep(2)
            if status_callback:
                status_callback(transcript.status.value)
            transcript = aai.Transcript.get_by_id(transcript.id)
    except Exception as exc:
        raise TranscriptionError(f"Fehler während der Transkription: {exc}") from exc

    if transcript.status == aai.TranscriptStatus.error:
        raise TranscriptionError(f"Transkription fehlgeschlagen: {transcript.error}")

    if not transcript.utterances:
        raise TranscriptionError(
            "Keine sprechergetrennten Abschnitte gefunden. Die Datei könnte stumm oder zu "
            "kurz sein, oder die Sprechererkennung ist fehlgeschlagen."
        )

    return [
        SimpleNamespace(speaker=u.speaker, text=u.text, start=u.start, end=u.end)
        for u in transcript.utterances
    ]


def get_utterances(audio_path, speakers_expected=None, force=False, status_callback=None):
    """Returns (utterances, from_cache)."""
    cache_path = cache_path_for(audio_path)
    if cache_path.exists() and not force:
        return load_cache(cache_path), True
    utterances = transcribe_via_api(audio_path, speakers_expected, status_callback)
    save_cache(cache_path, utterances)
    return utterances, False


def collect_unique_speakers(utterances):
    seen = []
    for utt in utterances:
        if utt.speaker not in seen:
            seen.append(utt.speaker)
    return seen


def format_timestamp(ms):
    total_seconds = ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def build_markdown(audio_path, utterances, speaker_mapping):
    lines = []
    lines.append(f"# Transcript: {audio_path.name}")
    lines.append("")
    lines.append(f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- **File:** {audio_path.name}")
    lines.append("- **Participants:**")
    seen_names = []
    for label, name in speaker_mapping.items():
        if name not in seen_names:
            seen_names.append(name)
            lines.append(f"  - {name}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for utt in utterances:
        timestamp = format_timestamp(utt.start)
        name = speaker_mapping.get(utt.speaker, f"Speaker {utt.speaker}")
        lines.append(f"**[{timestamp}] {name}:** {utt.text}")
        lines.append("")

    return "\n".join(lines)


def parse_names_arg(names_arg, speaker_labels):
    mapping = {}
    if not names_arg:
        return mapping
    for pair in names_arg.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        label, name = pair.split("=", 1)
        mapping[label.strip()] = name.strip()
    return mapping


# --- Persistent name storage -------------------------------------------------
# Two separate stores:
#  - known_names.json: a flat, de-duplicated list of every name ever used,
#    for autocomplete/reuse across *any* recording.
#  - <file>_speaker_names.json: the exact label->name mapping used for *this*
#    specific audio file, so reopening it pre-fills the previous choices.

def load_known_names():
    if KNOWN_NAMES_PATH.exists():
        try:
            names = json.loads(KNOWN_NAMES_PATH.read_text(encoding="utf-8"))
            return sorted(set(names))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _is_placeholder_name(name):
    return bool(re.fullmatch(r"Speaker [A-Z]", name or ""))


def add_known_names(names):
    existing = set(load_known_names())
    existing.update(n for n in names if n and not _is_placeholder_name(n))
    KNOWN_NAMES_PATH.write_text(
        json.dumps(sorted(existing), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_speaker_names(audio_path):
    names_path = names_path_for(audio_path)
    if names_path.exists():
        try:
            return json.loads(names_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_speaker_names(audio_path, mapping):
    names_path_for(audio_path).write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    add_known_names(mapping.values())


# --- Audio sample extraction (for "hear this speaker" playback) -------------

def get_ffmpeg_path():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def pick_sample_span(utterances, speaker_label, min_ms=3000, max_ms=15000, merge_gap_ms=1500):
    """Pick a [start_ms, end_ms) span that best represents this speaker's voice.

    Starts from their longest single utterance, then merges in adjacent
    utterances from the same speaker (if close together in time) until the
    span is at least min_ms long, capped at max_ms.
    """
    indices = [i for i, u in enumerate(utterances) if u.speaker == speaker_label]
    if not indices:
        return None

    anchor = max(indices, key=lambda i: utterances[i].end - utterances[i].start)
    start = utterances[anchor].start
    end = utterances[anchor].end

    i = anchor + 1
    while (
        (end - start) < min_ms
        and i < len(utterances)
        and utterances[i].speaker == speaker_label
        and (utterances[i].start - end) <= merge_gap_ms
    ):
        end = utterances[i].end
        i += 1

    i = anchor - 1
    while (
        (end - start) < min_ms
        and i >= 0
        and utterances[i].speaker == speaker_label
        and (start - utterances[i].end) <= merge_gap_ms
    ):
        start = utterances[i].start
        i -= 1

    if end - start > max_ms:
        end = start + max_ms

    return start, end


def extract_audio_clip(audio_path, start_ms, end_ms, output_path):
    ffmpeg = get_ffmpeg_path()
    start_s = max(start_ms, 0) / 1000
    duration_s = max(end_ms - start_ms, 500) / 1000
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        str(audio_path),
        "-t",
        f"{duration_s:.3f}",
        "-acodec",
        "libmp3lame",
        "-ar",
        "44100",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise TranscriptionError(
            f"Fehler beim Extrahieren der Audio-Probe: {result.stderr.decode(errors='ignore')[:300]}"
        )
