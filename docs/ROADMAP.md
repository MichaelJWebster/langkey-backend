# LangKey Roadmap

Living document. Updated as milestones complete and plans change.

Covers both repos. Kept here, and symlinked from `~/work/lang_app`, because the
umbrella directory is not version-controlled.

## Design principle

Design for many users; deploy for one (ADR-027). The prototype's deployment is deliberately
minimal and cheap to resize, but the architecture assumes LangKey may be commercialised and
have to scale — so single-user assumptions must not reach code or schema. Every milestone
below is sized for the current single learner; none of it should need rewriting to serve more.

## Learner profile (drives priorities)

- First user: Norwegian (Bokmål), CEFR A1–A2, 15–30 min/day.
- Skill priorities: listening, reading, speaking (in that order of interest, all three matter).
- Devices: desktop and mobile browsers equally.

## ✅ M0 — Foundations (done, 2026-09-26)

- Two repos: `langkey-backend` (FastAPI + Postgres + MinIO via docker compose), `langkey-frontend` (React + Vite + TS + Tailwind).
- Mock-but-realistic auth: bcrypt + JWT, seeded dev user (`dev@langkey.dev` / `langkey-dev`).
- End-to-end hello slice verified in browser: login → API → Postgres → UI.
- Backend test suite (pytest, in-memory SQLite).

## ✅ M1 — Core learning loop (done, 2026-09-26)

- Learning-domain schema: languages, items, user_cards (per-direction FSRS state), review_events (immutable log). Alembic migrations run at startup.
- FSRS scheduling via `py-fsrs` 6.x; new cards in batches of 10 per session with no daily cap (user preference: unlimited practice); recognition cards first, production (EN→NO) cards introduced once recognition graduates (staged directions).
- Seeded deck: 447 Claude-authored A1–A2 Bokmål entries (words + phrases, each with example sentence + translation, ordered by frequency/usefulness).
- API: `GET /reviews/queue`, `POST /reviews` (4-button FSRS rating), `GET /progress` (streak, established/learning counts).
- Frontend: flashcard session UI (space to reveal, 1–4 to grade, within-session requeue of learning cards), dashboard with streak/stats. Verified end-to-end in browser.

## ✅ M1.5 — Richer cards, speak button, content pipeline (done, 2026-09-27)

- Multiple examples per item (`item_examples` table; migration preserved the original example and all user progress). First 150 items have 3 hand-authored examples; remainder have 1 (backfill via M4 content generation).
- Speak button (word + every example) using browser speech synthesis (nb-NO voice), with `audio_key` plumbing ready for Piper-generated MinIO audio to take priority.
- Content pipeline: source files in repo (`seed_deck_nb.json` + `extra_examples_nb.json`) → `scripts/publish_deck.py` publishes Parquet to MinIO via DuckDB → API startup syncs Postgres from MinIO (local fallback), hash-gated and upsert-based so edits propagate without touching user progress.

## M2 — Listening (audio pipeline done 2026-09-27; exercises remaining)

- ✅ Piper TTS audio for all items + example sentences into MinIO (content-addressed; one-shot `docker compose --profile tts run --rm tts`), streamed via `/api/v1/audio/...`, played by the speak buttons with browser-TTS fallback.
- ✅ Topic focus (2026-09-27): 19 topic tags, focus-steered new-card selection, dashboard picker, session-end continue/change-focus, +24 themed starter phrases (meeting people / directions / shopping). Rating legend + single learning step (shorter sessions) same day.
- ✅ Exercise types (2026-09-27): dictation with diacritic-tolerant checking (a≈å marked "nearly right"), listen-and-choose with distractors; æøå on-screen buttons teaching the ⌥ shortcuts; attempts logged in exercise_attempts. **M2 complete.**

## M2.5 — Reading (2026-09-29)

- ✅ Reading library: interleaved Norwegian/English texts, sentence-aligned, stored per-sentence in Postgres with versioned translations; paged reader UI with hide-English/tap-to-peek mode and per-text resume position.
- ✅ Pipeline scripts (backend repo): `find_texts` (bokselskap/Gutenberg discovery) → `prepare_text` (extract → segment → translate via local Ollama gemma3:12b with echo-retry QC) → staging JSON review → `import_text`. First text: Askeladden som kapaad med Troldet (1904 orthography).
- Next candidates: more folk tales, then a Hamsun novel (multi-chapter via multiple URLs → sections); per-sentence audio; Claude repair pass on draft translations.

## M3 — Speaking

Researched 2026-10-04; see ADR-024 (conversation), ADR-025 (pronunciation) and the
illustrated write-up for the reasoning. Two distinct features, built in this order —
each step is useful on its own and makes the next one cheaper.

1. ✅ **Record and play back (done 2026-10-04).** Microphone button beside every speak
   button (headword and each example); takes go to R2 via `POST /api/v1/recordings` and
   play A/B against the Piper reference. No models. Takes accumulate rather than
   overwrite — they are the test corpus for steps 2–4. `user_recordings` table, keys
   scoped by user, authenticated stream route; the client fetches takes with the bearer
   token and plays them from a blob URL since `<audio>` cannot send one.
2. **NB-Whisper locally.** *Seam and service shell built 2026-10-05; the model has not
   been downloaded or run yet.* `app/speech/` holds the `SpeechToText` protocol, a null
   backend (the default — answers 503 rather than silently marking a drill wrong) and an
   HTTP backend; `LANGKEY_STT_BACKEND` chooses. `POST /api/v1/recordings/{id}/transcribe`
   transcribes a *saved* take, so the step-1 corpus is what the model gets judged on.
   `speech_service/` is the sibling container (ADR-032) — own Dockerfile, own
   `[dependency-groups] speech` for torch/transformers so the API image stays clean,
   lazy model load, `/health` that reports the model without downloading it.
   ADR-031 is enforced in code: `check_model_policy` refuses a generic Whisper outside
   dev.

   **Measured 2026-10-05 on 7 of Michael's own takes (2–3s each, known target
   sentences). The pipeline works; the accuracy does not yet.** Speed is a non-issue:
   0.3–0.5s per utterance on `small` and 0.8–1.1s on `medium`, on Metal, after a ~10s
   model load. Accuracy: `nb-whisper-small` got 1/7 exact, `nb-whisper-medium` 0/7 —
   bigger was *not* better, though its errors were at least plausible Norwegian
   (`Bananene er ikke modne ennå` → "Den andre er ikke ute ennå"). Decoding was ruled
   out first: decoded durations match the recorded ones within a frame and levels are
   healthy (peak 0.33–1.02, rms ~0.05), so this is the model, the L2 accent, or both.
   So step 2's condition — "checked against Michael's own voice before it is trusted" —
   is **not met**, and step 3 must not be built on exact string match.

   **Control experiment, same day — corrected after Michael listened.** The same seven
   sentences as Piper `talesyntese` clips transcribed **7/7 exact** on the same
   `nb-whisper-small`, against 1/7 for Michael's takes. The first write-up concluded
   from this that "the pipeline, the model, the codec and the recording quality are all
   ruled out in turn" and that only L2 accent remained. That was overstated in two ways.
   The Piper files were re-encoded by the same script that measured them, so the control
   could not have exposed a fault in what MediaRecorder writes or in a particular stored
   take; and Michael, played all seven back, identified one as audibly corrupted
   (`Bananene er ikke modne ennå`) while judging the other six usable. **The honest
   figure is 1/6 on audio he accepts, with one corrupted capture excluded.** What the
   re-runs do establish: original Piper WAVs and the opus re-encodes give identical
   transcripts, so the encoder adds nothing; and loudness is not the fault either, since
   `piper7` peaks above full scale with 6 clipped samples and still transcribes
   perfectly, as do 9 of the 181 saved takes that exceed full scale.

   **What the error actually is (2026-10-05).** Priming the decoder with the expected
   sentence via `prompt_ids` left the score unchanged at 1/6, and priming it with a
   *different* sentence never made it echo that sentence back (0/6) — twice it emitted
   nothing at all. So the model is neither guessing nor agreeing with whatever it is
   told: the sounds are genuinely absent from the audio. That is a pronunciation finding,
   not a recognition one. NB-Whisper is trained on 66,000 hours of *native* Norwegian and
   does not accommodate a learner — and the learner was copying a reference a third
   faster than he can speak (ADR-033), which is now fixed.

   This is direct evidence for ADR-025's reasoning rather than a setback: an STT model
   repairs wobbly speech into fluent Norwegian instead of reporting what it actually
   heard, so it cannot be the basis of pronunciation feedback — and it cannot be the
   basis of a say-it drill scored on string equality either, because the model answers
   confidently with the *wrong* fluent Norwegian (`Bananene er ikke modne ennå` →
   "Hva er manna ennå?") rather than signalling doubt.

   Levers not yet tried: `nb-whisper-large` (6 GB — bigger did not help from small to
   medium, but large is a real jump); scoring the audio's *likelihood against the known
   target* instead of free-decoding and comparing strings, which is forced alignment and
   therefore ADR-025's wav2vec2 CTC-GOP path rather than this one; and fuzzy or phonetic
   comparison if free decoding stays. Prompt-conditioning is now tried and rejected on
   evidence rather than on principle: it did not raise the score, and on two takes it
   silenced the model entirely.

   **Re-measure before trying anything else.** The 7 takes were all recorded against the
   old, too-fast pool-voice reference. ADR-033 changed what the record button plays, so
   the next corpus is recorded against the slowed primary voice — that is the cheapest
   remaining lever and it has to be pulled before `nb-whisper-large` or alignment work
   can be judged against anything.

   Weights live on the external drive: `HF_HOME=/Volumes/nvm_2tb/home/michaelw/work/huggingface`
   (3.8 GB for small + medium), and the speech service has its own Python 3.12 venv at
   `/Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv` (~1 GB with torch) so the
   API's `.venv` stays free of ML dependencies. **Remaining:** improve accuracy enough
   to trust, then wire the frontend.
3. **Say-it drills.** The crudest possible judgement: did the transcript match the target
   sentence? Closes the loop and lights up the Speaking card on the Aurora dashboard
   (ADR-023), which is disabled until this ships.
4. **Real pronunciation scoring.** Azure Pronunciation Assessment (`nb-NO`, per-phoneme)
   on trial first, to learn what good feedback feels like; then decide on a self-hosted
   wav2vec2 CTC-GOP + NoFA + NST stack. Always against a known target sentence. Presented
   as a hint, not a grade — the state of the art on Norwegian L2 severity rating is
   ~36% UAR (NOCASA 2025).
5. **Conversation last.** Turn-by-turn: record → NB-Whisper → constrained character with a
   couple of tools → reply text → Piper audio. The most work, and steps 1–2 build most of
   what it needs. Not an open-ended agent (ADR-026).

Open questions: hosting for NB-Whisper once it leaves the Mac (Workers AI
whisper-large-v3-turbo at $0.0005/audio-min is the cheap fallback, but generic Whisper
gives up the Norwegian accuracy margin); how speaking attempts should feed CEFR
estimation and FSRS, if at all.

## M4 — Intelligence & analytics

- LLM-generated personalised exercises from the learner's weak items (behind the exercise-generator interface).
- Agentic content jobs behind the staging/review gate (ADR-026): example sentences for items 151+, answer alternatives for typed cards, mock Norskprøve exercises (original texts only — the prove.hkdir.no samples are copyrighted; formats are not).
- Sentence mining from graded texts. ✅ CEFR estimation shipped 2026-10-02 (ADR-022).
- DuckLake over MinIO for analytics on review history.

## Known issues

- **Swallowed opening word (diagnosed 2026-10-04, fix deferred).** Some clips sound cut off at the start. **Not truncation and not playback** — Piper's `nvcc` pool voices under-articulate the first word, so it is mumbled rather than missing (ADR-028). 67 of 766 example clips affected, recorded in `data/audio_onset_issues.json`; `nvcc:4` accounts for a third of its own clips while `talesyntese` and `nvcc:6` are clean. `scripts/check_audio_onsets.py` now gates new content so the list cannot grow unnoticed. Fixing the existing 67 means regenerating them on another voice — deliberately deferred, since the voice is part of the content hash and re-picking reshuffles the corpus.

- **Mysterier: 33 source-side findings left (2026-10-05).** The deterministic gates (ADR-029) repaired all 7 translation-side faults, and what remains needs a human: 28 `norwegian_left` (a mix of genuinely untranslated sentences, place names, and one hallucinated place — Vardalskogene became "the forests of Vardsø") and 5 `digit_mismatch` where a print page number is fused inside a word (`jeg36var`) and so the glued-number check cannot see it. Inspect with `uv run python -m scripts.check_translation data/texts_staging/mysterier.json --show norwegian_left`. The fused-digit case wants an extraction fix in `prepare_text.py`, not a retranslation.

- **Folktale titles are hand-corrected, not derived (2026-10-05).** The 25 tales imported on 2026-10-05 have English titles set by hand where gemma3 was wrong, which was about half of them (ADR-030). They live only in the database: `scripts/retitle.py --list` shows them, and `import_text` preserves them on re-import. Two are deliberately null — `Fykomfei` and `Ringelihorn` are proper names, so the list shows the Norwegian alone. Worth a second opinion on the Nordland-dialect ones (`Oterhammen` → "The Otter Skin", `Jomfru Rosenving på Santavajasø` → "The Maiden Rosenving of Santavajasø").

## Proposed / unscheduled

- Real auth provider (replaces dev user store).
- Additional languages (schema is language-agnostic from M1).
- Streaks and gamification; progress dashboard v2.
- shadcn/ui + TanStack Query adoption in frontend as UI complexity grows.
