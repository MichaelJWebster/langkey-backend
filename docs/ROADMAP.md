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
- Seeded deck: 447 Claude-authored A1–A2 Bokmål entries (words + phrases, each with example sentence + translation, ordered by frequency/usefulness). *Now 860 — see M2.6.*
- API: `GET /reviews/queue`, `POST /reviews` (4-button FSRS rating), `GET /progress` (streak, established/learning counts).
- Frontend: flashcard session UI (space to reveal, 1–4 to grade, within-session requeue of learning cards), dashboard with streak/stats. Verified end-to-end in browser.

## ✅ M1.5 — Richer cards, speak button, content pipeline (done, 2026-09-27)

- Multiple examples per item (`item_examples` table; migration preserved the original example and all user progress). First 150 items have 3 hand-authored examples; remainder have 1 (backfill via M4 content generation).
- Speak button (word + every example) using browser speech synthesis (nb-NO voice), with `audio_key` plumbing ready for Piper-generated MinIO audio to take priority.
- Content pipeline: source files in repo (`seed_deck_nb.json` + `extra_examples_nb.json`) → `scripts/publish_deck.py` publishes Parquet to MinIO via DuckDB → API startup syncs Postgres from MinIO (local fallback), hash-gated and upsert-based so edits propagate without touching user progress.

## M2 — Listening (audio pipeline done 2026-09-27; exercises remaining)

- ✅ Piper TTS audio for all items + example sentences into MinIO (content-addressed; one-shot `docker compose --profile tts run --rm tts`), streamed via `/api/v1/audio/...`, played by the speak buttons with browser-TTS fallback.
- ✅ Topic focus (2026-09-27): 19 topic tags, focus-steered new-card selection, dashboard picker, session-end continue/change-focus, +24 themed starter phrases (meeting people / directions / shopping). Rating legend + single learning step (shorter sessions) same day. *Replaced 2026-10-10 by ten many-to-many categories that filter rather than sort — see M2.6 and ADR-038.*
- ✅ Exercise types (2026-09-27): dictation with diacritic-tolerant checking (a≈å marked "nearly right"), listen-and-choose with distractors; æøå on-screen buttons teaching the ⌥ shortcuts; attempts logged in exercise_attempts. **M2 complete.**

## M2.5 — Reading (2026-09-29)

- ✅ Reading library: interleaved Norwegian/English texts, sentence-aligned, stored per-sentence in Postgres with versioned translations; paged reader UI with hide-English/tap-to-peek mode and per-text resume position.
- ✅ Pipeline scripts (backend repo): `find_texts` (bokselskap/Gutenberg discovery) → `prepare_text` (extract → segment → translate via local Ollama gemma3:12b with echo-retry QC) → staging JSON review → `import_text`. First text: Askeladden som kapaad med Troldet (1904 orthography).
- Next candidates: more folk tales, then a Hamsun novel (multi-chapter via multiple URLs → sections); per-sentence audio; Claude repair pass on draft translations.

## ✅ M2.7 — Reading splits in two; A1 translation practice (2026-10-11)

Michael: *"I want to create subsections in Reading - The top one called "A1 Reading and translation" and the existing one with the novels and folktales in a section titled "Reading for interest (not A1 based)"."*

- ✅ Reading opens on two subsections. The library (novels, folktales) moves behind **Reading for interest (not A1 based)** unchanged; Escape and the back button now walk sections → category → text list → reader.
- ✅ **A1 Reading and translation**: a short Norwegian passage, an answer box, Check to reveal the reference translation, then one self-assessment with three answers (`got` / `partly` / `missed`). Session percentage counts a partial as a half.
- ✅ Glossary under every passage from the start, each word **in the inflected form the Norwegian uses** (`Hverdagen`, not `en hverdag`), lemma shown as a secondary note.
- ✅ Glossary is **per learner**: deck words missing from `user_glossary` are glossed alongside the words outside the course, marked `unseen` vs `new` and colour-coded. Overlapping glosses are suppressed by span, and an ambiguous surface form shows every meaning (`dusjer` → "a shower · to shower") instead of whichever deck entry claimed it first — `FormIndex.form_items`, added without disturbing `forms` or `match()`.
- ✅ New tables `passages`, `passage_glosses`, `passage_topics`, `passage_attempts` (migration `c5a81f3e74b2`). Passage rows are updated in place on a content edit so attempt history survives; glosses rebuilt by DELETE-then-INSERT to avoid the `(passage_id, position)` collision.
- ✅ An exchange is laid out as `Question:` / `Answer:` on separate lines, in both the Norwegian and the reference translation. Structure is authored (`lines` on the passage), not inferred from question marks — `a1-023` is two question marks from one speaker.
- ✅ Queue: up to 3 passages that went badly lead a session, then the ones needing the fewest words explained (measured per learner from `user_glossary`), authored sequence as tie-break. No FSRS — a passage has no recall interval worth modelling. See ADR-041.
- ✅ **75 passages** hand-authored and gated (mean 93% deck coverage, every unknown word glossed in the form used, banned-word list, length bounds, role checks on exchanges), in `data/a1_passages_nb.jsonl`. 17 are exchanges. Batches 2–3 added 2026-10-11; target is ~200.
- Found on the way: the deck-coverage gate was blind to the 548 multi-word deck entries, because `FormIndex` sends those to `.phrases` and not `.forms`. Fixed in the gate; `FormIndex` itself is correct for its own job.
- Next: the remaining ~125 passages; optionally generate them with Haiku 4.5 through the Batch API (under $1 for several hundred) rather than by hand, keeping the gates and the review step. The NorMistral second-pass grammar check needs its proposal diff fixed before its false-alarm rate can be trusted — it currently counts a verbatim quote of the original as a proposed change.

## ✅ M2.6 — A real A1 vocabulary, ten categories, a repetition deck (2026-10-10)

Michael: *"the different topics we have in there, like Nature & Weather and Health & Body
don't really have anything to do with the words being practised on in the sections."*
Diagnosis and the four changes are in ADR-038; the short version:

- **860 words, 742 of them tagged A1** (was 471). The 389 new entries come from NTNU's
  *Norsk for universitet og høgskole* A1 list, sorted into categories and given an
  example sentence each by local `gemma3:12b` via `scripts/build_vocab.py`, which gates
  every entry on part of speech, category validity, and the headword actually appearing
  in its own example. 9 of 389 failed that last gate and are hand-written — all
  reflexives, where `å barbere seg` correctly becomes *han barberer **seg***. The 118
  pre-existing A2 entries stay: Michael has progress on some, and the upsert key
  includes the wording.
- **Nineteen topics → ten categories, many-to-many.** `item_categories` replaces the
  single `items.topic` column; 406 of the 860 words sit in more than one category
  (1266 rows over 860 words). Migration `b3c7d50e9a14` backfills from the old topics
  and was verified both ways on a throwaway database.
- **A category now filters the deck instead of sorting it**, which is the actual fix to
  the complaint, and **progress bars** report established/learning/unmet per category
  and for the whole deck — counting each word once, so a word in two categories is
  learned in both at once.
- **Repetition deck** (`mode=repeat`, 40 cards): everything met, nothing new, due cards
  first, the rest sampled with weight for due/unestablished/lapsed/difficult words.

Audio for the new words: 4,221 Piper clips generated, nothing pruned.

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
   faster than he can speak (ADR-033), which is now fixed: the review panel plays and
   records against one clear voice at half the old pace.

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

   **Re-measured 2026-10-06, and the cheap lever was the big one.** 56 takes recorded
   against the slowed primary reference (ADR-033) with the capture fixed (ADR-034),
   43 of them sentences:

   | | exact | target words recovered |
   |---|---|---|
   | `small` | 19/43 (44%) | 69% |
   | `small`, peak-normalised | 21/43 (49%) | 69% |
   | `medium` | 21/43 (49%) | 73% |

   Against **1/7** on the old corpus. Three of the 43 are effectively silent (−49 to
   −72 dBFS speech), so the usable figure is 21/40. Two things this settles. Bigger is
   no longer worse — `medium` now edges `small`, where before it scored 0/7. And level
   is not the limiter: peak-normalising recovers one or two sentences, so disabling
   automatic gain (ADR-034) cost nothing in recognition even though it left the takes
   ~12 dB quieter. Not a controlled comparison — tonight's sentences are shorter and
   more familiar than the original seven — so the honest claim is that the combination
   of a copyable reference and a complete capture moved recognition from "unusable" to
   "roughly half", not that any one change did it. A controlled re-test means
   re-recording the original seven sentences.

   **The misses are now pronunciation data, which is the point.** Across both models,
   by how often a target word fails to appear: `klokka` 68% (heard as *klokke*,
   *slakke av*, *klakke*), `åtte` 64%, `ti` 61%, and `lærer`, `nye`, `kaffe`,
   `slutter` at 83–100%. Never lost across 4+ attempts: *bra, familien, fem, festen,
   for, gå, i, maten, må, nå, til, to, tusen, vi*. The greeting sentences score 10/10
   while every `klokka` sentence fails, so this is a short list of specific sounds
   rather than a general accent verdict — which is the shape of feedback step 4 is
   supposed to produce, arriving earlier than expected and without Azure.

   Weights live on the external drive: `HF_HOME=/Volumes/nvm_2tb/home/michaelw/work/huggingface`
   (3.8 GB for small + medium), and the speech service has its own Python 3.12 venv at
   `/Volumes/nvm_2tb/home/michaelw/work/langkey-speech-venv` (~1 GB with torch) so the
   API's `.venv` stays free of ML dependencies. **Remaining:** improve accuracy enough
   to trust, then wire the frontend.
3. **Say-it drills.** The crudest possible judgement: did the transcript match the target
   sentence? Closes the loop and lights up the Speaking card on the Aurora dashboard
   (ADR-023), which is disabled until this ships.
4. **Real pronunciation scoring — in the review panel, not a new screen.** Michael's ask:
   *"could we later on use that section to provide feedback about how well I'm pronouncing
   the expected words?"* That section is the right home, and it is already most of the way
   there: a known target sentence, a reference clip, and a saved take, which is exactly the
   input a scorer needs and the reason step 1 kept every take. Azure Pronunciation
   Assessment (`nb-NO`, per-phoneme) on trial first, to learn what good feedback feels like;
   then decide on a self-hosted wav2vec2 CTC-GOP + NoFA + NST stack. Always against a known
   target sentence, never free speech. Presented as a hint, not a grade — the state of the
   art on Norwegian L2 severity rating is ~36% UAR (NOCASA 2025), so the UI says "worth
   another go" and highlights which word drifted, never a score out of ten. Note the
   ordering this implies: scoring does **not** wait on step 3. Say-it drills need a model
   that transcribes a learner correctly, which NB-Whisper does not; per-phoneme scoring
   needs only alignment to a sentence already known, which is why ADR-025 picks it as the
   route to real feedback.
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

- **Cold-start timeout in production — fix before the next deploy (diagnosed 2026-10-11, not fixed).** Michael hit `Request failed (500)` on the live app; a retry a minute later worked. The app is not down — when warm, health and every dashboard endpoint answer 200, Neon is healthy (never suspends, 32 MB of a 1 GB limit) and R2's deck is untouched since 1 Oct. It is a cold boot that has grown too slow to answer in time.

  The container has `sleepAfter = '15m'` and there is one user, so most visits are cold. `lifespan` runs migrations → `seed_data()` → `backfill_glossary()` before uvicorn serves anything. Timed locally: imports 0.82s, alembic 0.05s, seed 0.24s, **`backfill_glossary` 2.19s**. That last one is the fault. Its own docstring calls it *"one-off: build glossary entries from review history predating the feature"*, but it runs on every boot, looping over every card with review history and issuing `session.get` per candidate word — **6,281 DB round trips, about 28 per reviewed card**, every one of them a no-op now that the 211 glossary rows exist.

  Production has 151 reviewed cards, so roughly **4,200 round trips per boot**. Locally that costs 2s because Postgres is on localhost; against Neon in Sydney it costs 4,200 × the round-trip time. Measured 18 ms from Michael's Mac to the Neon host — order-of-magnitude only, since the container's latency is its own — which lands near **75s**; even an optimistic 5 ms gives ~21s. **The cost scales with use**: 151 reviewed cards now against fewer at the 4 Oct deploy, which is why this only started biting.

  The diagnosis is also what the error message says: the UI prints the server's `detail` when there is one, and FastAPI's own 500 would read "Internal Server Error". `Request failed (500)` is what it prints when the body is not JSON at all — consistent with the container not yet being ready.

  Fixes, in order of value for effort: (1) stop running `backfill_glossary` on every boot — gate it on a flag in `app_meta` or move it to a script; it finished weeks ago and removes most of the boot cost for a few lines. (2) Move migrations and seeding out of startup into a deploy step — already the stated intention in the AWS-era plan, never carried over to Cloudflare. (3) `worker/index.ts` does a bare `getContainer(env.BACKEND).fetch(request)` with no retry or error handling, so a boot in progress surfaces as a raw 500; retrying, or returning something the UI can render as "waking up", turns an error into a wait.

- **Swallowed opening word (diagnosed 2026-10-04, fix deferred).** Some clips sound cut off at the start. **Not truncation and not playback** — Piper's `nvcc` pool voices under-articulate the first word, so it is mumbled rather than missing (ADR-028). 67 of 766 example clips affected, recorded in `data/audio_onset_issues.json`; `nvcc:4` accounts for a third of its own clips while `talesyntese` and `nvcc:6` are clean. `scripts/check_audio_onsets.py` now gates new content so the list cannot grow unnoticed. Fixing the existing 67 means regenerating them on another voice — deliberately deferred, since the voice is part of the content hash and re-picking reshuffles the corpus.

- **Mysterier: 33 source-side findings left (2026-10-05).** The deterministic gates (ADR-029) repaired all 7 translation-side faults, and what remains needs a human: 28 `norwegian_left` (a mix of genuinely untranslated sentences, place names, and one hallucinated place — Vardalskogene became "the forests of Vardsø") and 5 `digit_mismatch` where a print page number is fused inside a word (`jeg36var`) and so the glued-number check cannot see it. Inspect with `uv run python -m scripts.check_translation data/texts_staging/mysterier.json --show norwegian_left`. The fused-digit case wants an extraction fix in `prepare_text.py`, not a retranslation.

- **Folktale titles are hand-corrected, not derived (2026-10-05).** The 25 tales imported on 2026-10-05 have English titles set by hand where gemma3 was wrong, which was about half of them (ADR-030). They live only in the database: `scripts/retitle.py --list` shows them, and `import_text` preserves them on re-import. Two are deliberately null — `Fykomfei` and `Ringelihorn` are proper names, so the list shows the Norwegian alone. Worth a second opinion on the Nordland-dialect ones (`Oterhammen` → "The Otter Skin", `Jomfru Rosenving på Santavajasø` → "The Maiden Rosenving of Santavajasø").

## Proposed / unscheduled

- Real auth provider (replaces dev user store).
- Additional languages (schema is language-agnostic from M1).
- Streaks and gamification; progress dashboard v2.
- shadcn/ui + TanStack Query adoption in frontend as UI complexity grows.
