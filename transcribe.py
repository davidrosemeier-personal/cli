#!/usr/bin/env python3
"""CLI to transcribe and speaker-diarize meeting recordings via AssemblyAI.

Usage:
    python transcribe.py --file path/to/meeting.mp3 --speakers 2
"""

import argparse
import itertools
import sys
import threading
import time
from pathlib import Path

import core


def parse_args():
    parser = argparse.ArgumentParser(
        description="Transcribe and diarize a meeting audio file using AssemblyAI."
    )
    parser.add_argument("--file", required=True, help="Path to the audio file (mp3, wav, m4a).")
    parser.add_argument(
        "--speakers",
        type=int,
        default=None,
        help="Expected number of speakers (optional hint to improve diarization accuracy).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output Markdown path (default: <filename>_transcript.md next to the audio file).",
    )
    parser.add_argument(
        "--names",
        default=None,
        help='Non-interactive speaker name mapping, e.g. "A=David,B=Client". '
        "Skips the interactive naming prompt.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run transcription via the API even if a cached result exists.",
    )
    return parser.parse_args()


class Spinner:
    """Simple terminal spinner shown while the transcription job is running."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message="Processing"):
        self.message = message
        self._stop_event = threading.Event()
        self._thread = None
        self._status = "queued"

    def set_status(self, status):
        self._status = status

    def _spin(self):
        start = time.time()
        for frame in itertools.cycle(self.FRAMES):
            if self._stop_event.is_set():
                break
            elapsed = int(time.time() - start)
            sys.stdout.write(
                f"\r{frame} {self.message} [{self._status}] ({elapsed}s elapsed)   "
            )
            sys.stdout.flush()
            time.sleep(0.1)

    def __enter__(self):
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()


def prompt_speaker_mapping(speaker_labels, utterances, preset_mapping=None, saved_mapping=None):
    preset_mapping = preset_mapping or {}
    saved_mapping = saved_mapping or {}
    counts = {label: 0 for label in speaker_labels}
    for utt in utterances:
        counts[utt.speaker] += 1

    mapping = {}
    if preset_mapping:
        print("\nApplying provided speaker names:")
        for label in speaker_labels:
            default_name = f"Speaker {label}"
            name = preset_mapping.get(label, default_name)
            mapping[label] = name
            print(f'  "{default_name}" ({counts[label]} lines) -> {name}')
        return mapping

    known_names = core.load_known_names()
    if known_names:
        print(f"\nPreviously used names: {', '.join(known_names)}")
    print("Detected speakers:")
    for label in speaker_labels:
        default_name = saved_mapping.get(label, f"Speaker {label}")
        try:
            answer = input(
                f'  "Speaker {label}" ({counts[label]} lines) -> map to name '
                f'[Enter to keep "{default_name}"]: '
            ).strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        mapping[label] = answer if answer else default_name
    return mapping


def main():
    args = parse_args()

    try:
        audio_path = core.validate_file(args.file)
        core.load_api_key()
    except core.TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    cache_path = core.cache_path_for(audio_path)
    if cache_path.exists() and not args.force:
        print(f"Found cached transcript at '{cache_path.name}', skipping API call. Use --force to re-transcribe.")
    else:
        print(f"Submitting '{audio_path.name}' to AssemblyAI...")

    spinner_holder = {}

    def on_status(status):
        spinner = spinner_holder.get("spinner")
        if spinner:
            spinner.set_status(status)

    try:
        if cache_path.exists() and not args.force:
            utterances, _ = core.get_utterances(audio_path, args.speakers, args.force)
        else:
            with Spinner("Transcribing") as spinner:
                spinner_holder["spinner"] = spinner
                utterances, _ = core.get_utterances(
                    audio_path, args.speakers, args.force, status_callback=on_status
                )
    except core.TranscriptionError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user. The job may still be running on AssemblyAI's servers.")
        sys.exit(130)

    speaker_labels = core.collect_unique_speakers(utterances)
    preset_mapping = core.parse_names_arg(args.names, speaker_labels)
    saved_mapping = core.load_speaker_names(audio_path)
    speaker_mapping = prompt_speaker_mapping(speaker_labels, utterances, preset_mapping, saved_mapping)

    core.save_speaker_names(audio_path, speaker_mapping)

    markdown = core.build_markdown(audio_path, utterances, speaker_mapping)

    output_path = Path(args.output) if args.output else audio_path.with_name(
        f"{audio_path.stem}_transcript.md"
    )
    output_path.write_text(markdown, encoding="utf-8")

    print(f"\nDone. Transcript saved to: {output_path}")


if __name__ == "__main__":
    main()
