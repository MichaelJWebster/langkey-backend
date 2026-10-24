# Plan: the first conversation, built to collect its own corpus

*Executed 2026-10-24 — see ADR-051 for what was built and where it departs from this plan (dialogues authored directly, no name turn, same-meaning close calls accepted). Kept as the record of the reasoning.*

*Written 2026-10-24 for an executing session. ROADMAP M3 step 5. Read this whole
document, then the files it names, before writing code. Record decisions as ADRs in
`docs/ARCHITECTURE.md` (newest first; the next number is ADR-051) and keep
`docs/ROADMAP.md` step 5 current as you go.*

## What this is for

Two outputs, in this order of importance:

1. **A conversation corpus that does not exist yet.** Every recognition number we have
   comes from 569 takes of sentences read aloud after a reference. Nobody has recorded
   Michael *replying* to a character. The first conversation feature must log enough per
   turn — audio, transcript, candidates, scores, the decision, and **Michael's own
   confirmation of what he said** — that `scripts/bench_context.py` can re-score it
   offline and the decision rule can be tuned on real turns.
2. **The conversation skill, usable.** Turn-by-turn with a Norwegian-speaking character,
   in the Aurora dashboard's Conversation row (ADR-045), good enough that Michael uses it
   daily, because usage is what fills the corpus.

Prod is frozen (memory: `prod-frozen-redeploy-later`). **This is dev-only on the M2 Max.**
Do not deploy, migrate Neon, or touch Cloudflare. Commit and push to GitHub after each
phase.

## What is already decided, and the evidence

Read these before anything else — they are short and they rule out most of the obvious
designs:

- **ADR-024, ADR-031, ADR-032** — NB-Whisper (`medium`), Norwegian-trained only, speech
  runs as its own service behind `app/speech/` (`SpeechToText`, `WordAligner`
  protocols; `none` / `http` backends). The conversation LLM is a *client*, never our
  container.
- **ADR-026** — agents belong in the content pipeline, not the learner's request path. The
  conversation partner is "a tightly constrained character with two or three tools", not
  an agent. **For v1 there is no LLM in the request path at all** (see Design). The LLM
  drafts dialogues offline, behind Michael's review, like the passages (ADR-041) and
  grammar (ADR-050).
- **ADR-036, ADR-043, ADR-044, ADR-045** — the pronunciation tally, forced alignment,
  the speaking ring, and Conversation as its own skill, kept out of the overall level
  until something trains it.
- **ROADMAP step 2 and step 5 (2026-10-24 entries)** — the benchmarks this plan rests on.
  The numbers that matter, all on 506 sentence takes, Whisper `medium` free decoding at
  34% exact / 56% gist:

  | candidate set (target + …) | set alone | combined with transcript, per-letter margin 0.3 / 0.5 | wrong answer accepted off-script |
  |---|---|---|---|
  | 4 distinct deck sentences | 95% | 81% / 88% | 11% / 12% |
  | 7 Haiku-written distinct alternatives | 96% | 82% / 89% | 10% / 12% |
  | 4 one-word near-spellings (*hjelpe / hjalp*) | 83% | 72% / 78% | 44% / 56% |

  - A bigger Whisper (`large`) does not help: a third of sentences lose their gist on
    both sizes. Whisper's n-best adds nothing. wav2vec2's own transcript is unusable as
    text. A GOP-style threshold did worse than comparing against the transcript.
  - **The answer cannot be anticipated from the question** (Haiku's eight likeliest A1
    answers contained the real one 4% exactly, 15% by gist), and **proposing after
    hearing** (question + misheard transcript → candidates → CTC) only lifts exact to 44%
    with 19% confidently wrong. So the candidate set must come from the turn's design.
  - Scoring is log P(text | audio) under `nb-wav2vec2-300m-bokmaal`, **divided by letter
    count** (the raw sum favours shorter texts). ~0.16 s for all candidates of a take, on
    the CPU. Code: `ctc_scorer` in `scripts/bench_context.py`.

The design that follows is the only one the evidence supports: **turns whose answer space
is small, known in advance, and distinct.**

## Design (v1)

**Scripted dialogues as content.** A dialogue is a shallow tree authored in
`data/dialogues_nb.json`, synced into Postgres at startup by slug like the grammar units
(`app/seed.py::_sync_grammar` is the template; `app/content.py` loads and hashes). Each
character turn carries its Norwegian line, an English gloss, and 3–6 **expected learner
answers**, each with its own English gloss and the id of the turn it leads to. Every node
also accepts two universal answers — *"Jeg forstår ikke"* (character repeats, slower) and
*"Kan du gjenta?"* — and every branch ends somewhere; a dead end re-asks.

**Character audio is pre-generated Piper**, not runtime TTS. Because the lines are
content, `docker/piper/generate_audio.py` can synthesise them exactly as it does deck
sentences: primary voice (`talesyntese`), the five imitation rungs so `SpeedChips`
works unchanged, leading gap capped (ADR-048), onset gate (ADR-028). Keys from
`app/audio.py::audio_object_key`. No new audio path.

**A learner turn** = record (existing `RecordButton` / `recording.ts`) → upload →
Whisper `medium` greedy transcript → CTC-score {expected answers ∪ universal answers ∪
transcript} per letter → decision → character reply (pre-generated clip) → log. No LLM.

**The decision rule** (pure function, `app/conversation.py`, tested against synthetic
scores first and the real corpus later):

- Let `e` = best expected answer, `t` = the transcript's own score, `e2` = second-best
  expected, all per letter. `MARGIN = 0.4` nats/letter (between the benchmark's 0.3 and
  0.5 columns), `CLEAR = 0.15` nats/letter.
- `accepted` if `e ≥ t − MARGIN` and `e − e2 ≥ CLEAR`: the character replies and **echoes
  the content** (*"I Oslo! Hyggelig."*) — that echo is the confirmation. The accepted
  answer is shown small under the learner's bubble (*Du sa: Jeg bor i Oslo.*), tappable to
  correct.
- `choice` if `e ≥ t − MARGIN` but the top two are within `CLEAR`: the character asks
  *"Sa du … eller …?"* with the two as buttons, plus *"Noe annet"* (something else) which
  opens the full list of expected answers for this turn.
- `unclear` otherwise: the character says *"Unnskyld, jeg hørte ikke."* and the UI offers
  **try again** or **tap your answer** (the full list). Tapping is a confirmation too.

Every path ends with a confirmed answer id (or "none of these" with an optional typed
line), which is the ground-truth label for the corpus.

**Pronunciation feedback rides along, but after the fact.** Once an answer is confirmed,
`app/pronunciation.compare(confirmed_text, transcript)` gives word marks exactly as the
pronunciation flow does, and they are appended to `pronunciation_attempts` with the new
`recording_id` (see schema). Conversation then feeds the tally that steers pronunciation
practice (ADR-037) at no extra cost. Do **not** show the marks during the conversation —
that is the pronunciation screen's job — but keep the transcript on the turn row.

**No scoring of the Conversation ring in v1.** ADR-045 says its scoring arrives with the
feature; the honest version is "arrives with the corpus". Log what a score would need
(turns completed, accepted-first-time rate, dialogues finished) and leave the ring
reporting *not trained* until there is a measured basis. Update the dashboard subtitle from
"not built yet" to what it does.

## Schema

New tables, Alembic migration chained to the current head (`uv run alembic revision
--autogenerate` after adding the models; check the chain is linear). Use `JSON` columns
as the existing models do; tests run on in-memory SQLite.

- `dialogues` (`id`, `language_code`, `slug` unique, `title`, `blurb`, `seq`, `nodes`
  JSON — the whole tree, since nothing queries inside it) — synced by slug.
- `conversations` (`id` uuid, `user_id`, `dialogue_id`, `started_at`, `finished_at`,
  `node_id` current).
- `conversation_turns` (`id` uuid, `conversation_id`, `user_id`, `node_id`, `seq`,
  `object_key` — `conversations/<user>/<uuid>.<ext>` in the same bucket as recordings,
  `content_type`, `size_bytes`, `duration_ms`, `transcript`, `transcript_model`,
  `candidates` JSON — `[{id|null, text, ctc, letters}]` including the transcript entry,
  `scorer_model`, `decision` — `accepted|choice|unclear`, `picked_id`, `confirmed_id`,
  `confirmed_text` for "none of these", `confirmed_how` — `echo|choice|list|typed|retry`,
  `transcribe_ms`, `score_ms`, `created_at`).
- `pronunciation_attempts.recording_id` is a FK to `user_recordings`. **Do not point it at
  a conversation turn.** Either add a nullable `turn_id` FK alongside and relax the
  not-null on `recording_id` with a check constraint (one of the two set), or write the
  conversation marks to a sibling table. Prefer the first; it keeps `_history` in
  `app/routes/recordings.py` and the speaking-practice weighting reading one table.
  Record the choice as part of the ADR.

## The speech side

**New endpoint in `speech_service/main.py`: `POST /score`** — multipart `audio` plus
`candidates` (JSON list of strings) → `[{text, ctc, letters}]`, plus `model`,
`duration_ms`. Implement the scoring in `speech_service/scorer.py` by **moving**
`ctc_scorer` out of `scripts/bench_context.py` into the service and importing it back
into the script, so the benchmark and production run the same code. It uses the aligner's
already-loaded model (`aligner.load`), so no new weights and no second Metal context.
Keep `None` for a candidate with no letters the model knows or more letters than frames.

**The seam** (`app/speech/`): add a `CandidateScorer` protocol next to `SpeechToText`
and `WordAligner` (`base.py`), an `HttpScorer` in `http.py`, a null in `null.py`, and
`get_scorer` in `__init__.py`/`registry.py` following `get_aligner`. Same error pair
(`TranscriptionUnavailable` → 503 "not available", `TranscriptionError` → 502). With the
backend `none`, the conversation route must still work: it falls through to `unclear`
and offers the tap-your-answer list, so the feature is absent-but-not-broken like the
pronunciation check. Tests: mirror `tests/test_speech_seam.py` and
`tests/test_speech_service.py` (the service tests run without torch by faking the module
functions — follow that pattern).

The dev service is started by `scripts/run_speech.sh` / `./start.sh` from the umbrella and
answers on `127.0.0.1:8001`; `.env` already points `LANGKEY_STT_URL` at it. Memory rule:
do not stop dev services while working; restart the speech service only when its code
changed, and then with `run_speech.sh --daemon`.

## Content

**Format** (`data/dialogues_nb.json`): a list of dialogues; each `{slug, title, blurb,
seq, start, nodes: {id: {say, say_en, answers: [{id, text, en, next}], end?: bool}}}`.
Keep ids short and stable (attempt history will point at them).

**Drafting script** `scripts/draft_dialogue.py`: offline, behind the review gate, like
`scripts/prepare_text.py`. Given a scenario title and the learner's known vocabulary
(`user_glossary` for the dev user, or the deck's first N items), it asks the LLM for a
dialogue in this format, then runs **gates** and writes to `data/dialogues_staging/`
(gitignored) for Michael to read before it moves to `data/`. Use the Claude API
(`uv run --with anthropic`, as `bench_context.py llm` does; model per the project's
`claude-api` skill, Haiku is fine for drafting). Gates, all deterministic:

1. **Distinctness**: for every node, no two answers (including the universal ones) may
   differ by only one word whose spelling similarity is ≥ 0.6 — the near-spelling failure
   (`SequenceMatcher`, same measure as `slot_variants` in `bench_context.py`). Fail loudly
   with the offending pair.
2. **Vocabulary**: report words outside the deck (`app/glossary.py` matching); not a hard
   fail, but listed for review.
3. **Reachability**: every `next` exists; every node is reachable from `start`; at least
   one `end`.
4. **Piper**: after generation, the onset gate (`scripts/check_audio_onsets.py`) covers the
   new clips like any other.

**Scenarios for v1** (six, 6–10 character turns each, A1): *Hei — meeting someone*; *På
kafé — ordering*; *Hvor bor du? — where you live and family*; *Klokka — making an
appointment*; *I butikken — shopping*; *Veien — asking directions*. Author answers to
include fragments where a native would use them (*"I Oslo."*, *"Klokka ti."*) alongside
full sentences, since Michael will say both. Where a slot has personal values (his town,
family members), use values from his glossary rather than inventing names.

**Audio**: extend `docker/piper/generate_audio.py` to read `data/dialogues_nb.json`
(mounted; see how the deck Parquet is read) and synthesise every `say` on the primary
voice at the five imitation rungs; keep it idempotent and let the prune step know about
the new texts so it does not delete them. Then `docker compose --profile tts run --rm tts`
and `uv run python -m scripts.check_audio_onsets --update`.

## API

`app/routes/conversation.py`, prefix `/api/v1/conversation`, auth as elsewhere:

- `GET /dialogues` — list with progress (finished count per dialogue for this user).
- `POST /start {dialogue_slug}` → conversation id, first node (`say`, `say_en`, audio
  speeds via `app/routes/speaking.py::_speeds`, answers with ids and glosses — the client
  needs them for the tap-your-answer list, and showing glosses is a UI choice).
- `POST /{id}/turn` — multipart audio (reuse `create_recording`'s content-type handling,
  `_base_type`, size limit, S3 put) → runs transcribe + score → stores the turn → returns
  `{turn_id, decision, picked, alternatives (for choice), transcript, next: node or null}`.
  On `accepted`, `next` is the character's reply; on `choice`/`unclear`, `next` is null
  until confirmation.
- `POST /{id}/turn/{turn_id}/confirm {answer_id | text | retry}` → records
  `confirmed_*`, appends pronunciation marks, advances the node, returns `next`.
- `POST /{id}/finish`.

Transcription and scoring happen on the critical path (they are the turn); alignment does
not run here. Save the audio **before** calling the speech service, as the recordings route
does — a turn whose audio is saved is corpus even if the service is down.

## Frontend

`langkey-frontend/src/Conversation.tsx` (+ `Conversation.test.tsx` with vitest, like
`SpeakingSession.test.tsx`), mounted where `Dashboard.tsx` renders the conversation
placeholder (`skill === 'conversation'`). A chat column: character bubbles with a play
button (`speak(text, audioUrl)` from `speech.ts`, `SpeedChips` for the rung), learner
bubbles showing the confirmed answer (and the raw transcript in a muted line **only when
it differs**, tappable to correct), the `RecordButton` at the bottom, and the three
decision states as described. Keyboard: space to record, as `SpeakingSession` does.
Start with a dialogue picker (title, blurb, times finished). No prettier in this repo —
match the house style by hand; typecheck with `npx tsc -b`; `npm test`.

Latency budget per turn on the Mac: upload + Whisper `medium` ~1 s + scoring ~0.2 s. Show a
"hører etter…" state; do not add a spinner that implies more.

## Corpus export

`scripts/export_conversations.py` → the `scripts/bench_stt.py` directory layout
(`corpus.tsv` + `takes/`), with the **confirmed answer as the target** and the node's
expected answers as the candidate set, so `bench_context.py ctc` + `score` run unchanged
on real turns. Add a `--since` flag. The first question to answer with it, after a week of
use: does the margin/clear banding hold on replies, and what is the real off-script rate
(turns confirmed as "none of these")?

## Order of work, with the check that ends each phase

1. **Read.** This file; the ADRs above; `app/speech/*`, `speech_service/*`,
   `app/routes/recordings.py`, `app/routes/speaking.py`, `app/seed.py`,
   `app/models/grammar.py`, `scripts/bench_context.py`, `langkey-frontend/src/{SpeakingSession,RecordButton,recording,speech,SpeedChips}.ts(x)`,
   `tests/conftest.py`, `tests/test_speech_service.py`. Confirm dev services are up
   (`curl localhost:8001/health`, `localhost:8000/docs`); start with `./start.sh` from the
   umbrella if not. *Check: you can state in two sentences why there is no LLM in the
   request path and why the candidate set must be authored.*
2. **Scorer in the service + seam.** `speech_service/scorer.py`, `POST /score`,
   `CandidateScorer` + http/null + registry, tests. Move `ctc_scorer` and re-point the
   benchmark; `uv run python -m scripts.bench_context --dir /tmp/langkey-stt-bench score`
   must print the same numbers as before (the bench dir may need re-exporting:
   `scripts.bench_stt export`, `bench_context pool`, `run`, `llm` is optional, `ctc`).
   *Check: `curl -F audio=@take.webm -F 'candidates=["Jeg bor i Oslo.","Hei"]' localhost:8001/score`
   returns per-candidate scores; `uv run pytest` green.*
3. **Decision rule.** `app/conversation.py` pure functions + `tests/test_conversation.py`
   covering accepted/choice/unclear, a `None` transcript score, a scorer that is down.
   *Check: tests encode the three bands with the constants named above.*
4. **Content.** Format, loader + hash in `app/content.py`, `_sync_dialogues` in `seed.py`,
   `Dialogue` model + migration. Drafting script with gates. **Draft one dialogue (*Hei*),
   have Michael read it, move it to `data/`**, generate its audio, run the onset gate.
   *Check: `GET /api/v1/conversation/dialogues` lists it; its clips play from
   `/api/v1/audio/...`.*
5. **Turn routes + models.** `conversations`, `conversation_turns`, the
   `pronunciation_attempts` decision, routes, tests with the fake S3 and fake
   transcriber/scorer from `tests/test_recordings.py` / `test_speaking.py`.
   *Check: a scripted test walks a two-node dialogue through accepted → confirm → finish.*
6. **Frontend.** Picker, chat, three states, correction. *Check: Michael completes *Hei*
   end to end in Chrome, with the speech service running, and at least one turn each lands
   in `accepted`, `choice` and `unclear` during a deliberate test (mumble one).* Watch
   the real browser, not jsdom (memory: `controls-must-exercise-the-path`).
7. **Remaining five dialogues** through the same draft → review → audio path.
8. **Export + write-up.** `export_conversations.py`; ADR-051 (the design, the schema
   decision, the constants, what v1 deliberately leaves out); ROADMAP step 5 marked in
   progress with what shipped; dashboard subtitle. Update the memory file
   `langkey-project.md` status line.

Commit at the end of each phase, push, no deploy.

## Not in v1 — do not drift into these

- A runtime LLM, including the post-hoc proposal. Measured at 44% exact with 19%
  confident errors; the corpus may change that, the feature does not.
- Open-ended turns, free chat, or an agent. ADR-026.
- Runtime TTS. Lines are content; Piper runs offline.
- Scoring the Conversation ring. Log for it; do not invent it.
- Streaming recognition. Turn-by-turn is the design (ADR-024).
- A different Whisper size, n-best, or wav2vec2 as transcript. All measured; all out.
- Any production work.

## Questions for Michael before phase 4 (defaults in brackets)

- Default playback rung for the character — the remembered imitation speed, or always
  the natural rung 1.6? [remembered speed, like pronunciation]
- English glosses visible by default, or on tap? [on tap — it is interaction practice]
- Show the raw transcript when it matches the confirmed answer? [no; only when it differs]
- Which town / names should the *Hvor bor du?* dialogue use for his slots? [ask]
- Six scenarios the right first set, or swap any? [ask]

## Conventions that bite

- Python: `uv run pytest`; `uvx ruff check` must pass (the repo uses `BLE001` with a
  `noqa` comment where a broad catch is deliberate; `B023` is silenced per file only in
  `bench_context.py`). Lazy-import torch/transformers in anything under `app/` — the API
  venv has neither (ADR-032). Speech venv:
  `/Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv`, `HF_HOME=/Volumes/nvm_2tb/home/michaelw/work/huggingface`.
- Frontend: no prettier; `npx tsc -b` is the typecheck that counts; tests next to source.
- `tests/conftest.py` hides the developer's `.env` from the suite; seam tests assert the
  `none` backend by default.
- transformers 5.18: Whisper's `generate` ignores `num_return_sequences`; irrelevant here
  because the turn uses greedy, but do not "improve" the transcriber to beam search.
- Don't stop MinIO/Postgres/API; Michael uses the app while you work. Don't wipe the DB
  volume.
- Commit messages end with the Co-Authored-By line the session provides; always push.
