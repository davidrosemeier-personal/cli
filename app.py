"""Streamlit GUI for uploading a meeting recording, transcribing it with
speaker diarization, and naming speakers by listening to voice samples.

Run with: streamlit run app.py
"""

import tempfile
from pathlib import Path

import streamlit as st

import core

st.set_page_config(page_title="Plaud Transkript", page_icon="🎙️", layout="centered")

UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
CLIPS_DIR = Path(tempfile.gettempdir()) / "plaud_transcript_clips"
CLIPS_DIR.mkdir(exist_ok=True)

st.title("🎙️ Plaud Transkript")
st.caption("Audiodatei hochladen, transkribieren, Sprecher per Hörprobe benennen.")

uploaded_file = st.file_uploader("Audiodatei (mp3, wav, m4a)", type=["mp3", "wav", "m4a"])

if uploaded_file is not None:
    audio_path = UPLOADS_DIR / uploaded_file.name

    if st.session_state.get("last_uploaded_name") != uploaded_file.name:
        audio_path.write_bytes(uploaded_file.getvalue())
        st.session_state["last_uploaded_name"] = uploaded_file.name
        st.session_state.pop("utterances", None)
        st.session_state.pop("markdown_output", None)

    st.audio(str(audio_path))

    col1, col2 = st.columns(2)
    with col1:
        speakers_hint = st.number_input(
            "Erwartete Anzahl Sprecher (optional)", min_value=0, max_value=20, value=0, step=1
        )
    with col2:
        force = st.checkbox("Neu transkribieren (Cache ignorieren)")

    if st.button("Transkribieren", type="primary"):
        status_box = st.empty()

        def on_status(status):
            status_box.info(f"Status: {status}")

        try:
            with st.spinner("Transkribiere über AssemblyAI... (kann einige Minuten dauern)"):
                utterances, from_cache = core.get_utterances(
                    audio_path,
                    speakers_expected=speakers_hint or None,
                    force=force,
                    status_callback=on_status,
                )
            status_box.empty()
            st.session_state["utterances"] = utterances
            st.session_state["audio_path"] = audio_path
            st.session_state.pop("markdown_output", None)
            if from_cache:
                st.info("Aus Cache geladen (keine erneute API-Anfrage, keine zusätzlichen Kosten).")
            else:
                st.success("Transkription abgeschlossen.")
        except core.TranscriptionError as exc:
            status_box.empty()
            st.error(str(exc))

if "utterances" in st.session_state:
    utterances = st.session_state["utterances"]
    audio_path = st.session_state["audio_path"]
    speaker_labels = core.collect_unique_speakers(utterances)
    known_names = core.load_known_names()
    saved_mapping = core.load_speaker_names(audio_path)

    st.subheader("Sprecher benennen")
    st.caption("Höre dir eine Probe an und ordne jedem Sprecher einen Namen zu.")

    NO_CHANGE = "— bekannten Namen wählen —"

    with st.form(key=f"naming_form_{audio_path.stem}"):
        text_inputs = {}
        select_inputs = {}

        for label in speaker_labels:
            count = sum(1 for u in utterances if u.speaker == label)
            with st.container(border=True):
                st.markdown(f"**Speaker {label}** &nbsp;·&nbsp; {count} Redebeiträge")

                span = core.pick_sample_span(utterances, label)
                if span is not None:
                    clip_path = CLIPS_DIR / f"{audio_path.stem}_{label}_{span[0]}_{span[1]}.mp3"
                    if not clip_path.exists():
                        try:
                            core.extract_audio_clip(audio_path, span[0], span[1], clip_path)
                        except core.TranscriptionError as exc:
                            st.warning(str(exc))
                    if clip_path.exists():
                        st.audio(str(clip_path))

                default_name = saved_mapping.get(label)
                options = [NO_CHANGE] + known_names
                default_index = 0
                if default_name and default_name in known_names:
                    default_index = options.index(default_name)

                select_inputs[label] = st.selectbox(
                    f"Bekannten Namen wählen für Speaker {label}",
                    options,
                    index=default_index,
                    key=f"select_{audio_path.stem}_{label}",
                )
                prefill = default_name if default_name and default_name not in known_names else ""
                text_inputs[label] = st.text_input(
                    f"Oder neuen Namen eintragen für Speaker {label}",
                    value=prefill,
                    key=f"text_{audio_path.stem}_{label}",
                )

        submitted = st.form_submit_button("Transkript erstellen", type="primary")

    if submitted:
        name_choices = {}
        for label in speaker_labels:
            typed = text_inputs[label].strip()
            selected = select_inputs[label]
            if typed:
                name_choices[label] = typed
            elif selected != NO_CHANGE:
                name_choices[label] = selected
            else:
                name_choices[label] = f"Speaker {label}"

        core.save_speaker_names(audio_path, name_choices)
        markdown = core.build_markdown(audio_path, utterances, name_choices)
        output_path = audio_path.with_name(f"{audio_path.stem}_transcript.md")
        output_path.write_text(markdown, encoding="utf-8")
        st.session_state["markdown_output"] = markdown
        st.session_state["output_path"] = output_path
        st.success(f"Transkript gespeichert: {output_path}")

if "markdown_output" in st.session_state:
    st.subheader("Ergebnis")
    st.download_button(
        "Markdown herunterladen",
        st.session_state["markdown_output"],
        file_name=Path(st.session_state["output_path"]).name,
        mime="text/markdown",
    )
    with st.expander("Vorschau", expanded=True):
        st.markdown(st.session_state["markdown_output"])
