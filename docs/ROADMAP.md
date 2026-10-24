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

## ✅ M2.9 — Grammar practice (2026-10-22)

Michael: *"I'd like to have some exercises with grammar in a new section grammar practice"*, then *"a diagrammatic or graphical representation of the grammar rules to be pinned to the top of the page and exercises under it"*, *"different sections within V2 word order"* and *"one exercise at a time ... 10 exercises before we ask if the user wants to continue, or go back"*.

- ✅ **Grammar practice** row on the skills list, above Games. No CEFR ring, for the reason Games has none (ADR-042).
- ✅ Six A1 topics: V2 word order, yes/no questions, negation, nouns (en/ei/et, definite, plural), tenses, pronouns & determiners. Designed as a clickable prototype first (claude.ai/artifact/HtZGE7VjNFv1p7iSm343FK), then built as prototyped.
- ✅ **V2 is split into seven rules plus a mixed review**: subject first, small adverbs, time first, place first, two verbs, object first, og/men/eller. Each card on the V2 menu carries its own diagram, whose column headings name the role of each part (Subject · Verb · Object · Time …) and mark the verb as slot 2. 10 exercises per rule; the review draws on all 70. The other five topics are one unit each with 8 exercises, to be refined by Michael later.
- ✅ **The rule is pinned above the exercise.** Desktop: full diagram in the sticky header, Hide/Show remembered. Phone: a compact strip of the rule in the header; the full diagram slides in from the right (tap the strip or the edge tab, or swipe from the edge) and swipes, taps or Escapes away. "See the rule" on a wrong answer opens it at the row that explains it and flashes that row.
- ✅ **Tooltips on every grammar word** (subject, verb, conjugated verb, object, adverbial phrase, adverb, infinitive, linking word, inversion, slot 2, …): hover on a mouse, tap on a touch screen. Content marks them up as `[[key]]`/`[[key|label]]`; a backend test holds the keys to a known list.
- ✅ Rounds of ten, one exercise at a time, Next between them. Four kinds: choose, build (word tiles), transform/fix (typed, with æ ø å buttons), fill. Marked in the browser: case, punctuation and spacing ignored, a missing æ ø å wrong but named as such (ADR-040). Results screen: score, the misses with their answers, **Practise 10 more** or back to the menu. Missed exercises lead the next round.
- ✅ New tables `grammar_units`, `grammar_exercises`, `grammar_attempts` (migration `a3f8c61d2e47`), synced from `data/grammar_nb.json` by slug, updated in place so answers keep their exercise. Content gates in `tests/test_grammar.py`: a build's answer uses exactly its tiles, a choice offers its answer, a cue is where the answer starts, a fix starts from something wrong. See ADR-050.
- Found on the way: StrictMode's double fetch returned two differently shuffled rounds and the first card kept its typed box from the other one. Stale fetches are ignored and cards are keyed per round.
- Next: Michael to review the other five topics and split them into rules the way V2 is.

## ✅ M2.8 — Games (2026-10-17)

Michael: *"I'd like a new section after speaking on the main page. Call it games. In games I will have several sections with different gamified learning features."*

- ✅ **Games** row on the skills list, below Speaking. No CEFR ring and no can-do box — his call when asked — because there is no descriptor for a matching game and a lookalike ring would devalue the real ones.
- ✅ Three levels: subsections (derived from each set's `game` kind) → sets → play. One subsection, **Flashcards**, and one set, **Question words**.
- ✅ **Question words**: 10 pairs — what/who/where/when/why/how plus the `hvor`-compounds *hvor mye, hvor mange, hvor lenge, hvor langt*. The last two carry notes (time / distance) because "how long" alone would appear twice and make the board unplayable. The note shows on the English card only — repeating it on `hvor lenge` printed the answer on the card.
- ✅ Matching: English on the left, Norwegian on the right, both shuffled with a guarantee that **no card faces its own translation**. Pick either side first; a wrong pairing costs one mistake and nothing else; switching sides mid-pick is free.
- ✅ New tables `game_sets`, `game_pairs`, `game_rounds` (migration `e7b2d91a5c48`), seeded from `data/games_nb.jsonl` by the same hash-gated sync as the deck and the passages. Set rows update in place so rounds keep their target.
- ✅ A wrong pairing marks **only the two cards picked**. It first marked both pairs, which lit the two right answers as well — Michael: *“I noticed the UI flashed the correct option when I picked incorrectly.”* Cards are keyed by side, not by pair.
- ✅ The finished board shows the best *including the round just played*. It showed the best from before the set was opened, because the open set was a copy rather than a lookup into the reloaded list.
- ✅ **New best** badge on the finished board, with what it beat ("★ A new best · beat 2 mistakes, 40s"). Not shown on a first round, which has beaten nothing. The server decides it, since it owns the tiebreak and the rounding.
- ✅ Best score per set: fewest mistakes, then quickest. Only completed rounds count. Games feeds nothing else — not FSRS, not the glossary, not the CEFR rings. See ADR-042.
- Next: more sets (numbers, days, prepositions, irregular verbs), and a second game kind when one is wanted.

## ✅ M2.7 — Reading splits in two; A1 translation practice (2026-10-11)

Michael: *"I want to create subsections in Reading - The top one called "A1 Reading and translation" and the existing one with the novels and folktales in a section titled "Reading for interest (not A1 based)"."*

- ✅ Reading opens on two subsections. The library (novels, folktales) moves behind **Reading for interest (not A1 based)** unchanged; Escape and the back button now walk sections → category → text list → reader.
- ✅ **A1 Reading and translation**: a short Norwegian passage, an answer box, Check to reveal the reference translation, then one self-assessment with three answers (`got` / `partly` / `missed`). Session percentage counts a partial as a half.
- ✅ Glossary under every passage from the start, each word **in the inflected form the Norwegian uses** (`Hverdagen`, not `en hverdag`), lemma shown as a secondary note.
- ✅ Glossary is **per learner**: deck words missing from `user_glossary` are glossed alongside the words outside the course, marked `unseen` vs `new` and colour-coded. Overlapping glosses are suppressed by span, and an ambiguous surface form shows every meaning (`dusjer` → "a shower · to shower") instead of whichever deck entry claimed it first — `FormIndex.form_items`, added without disturbing `forms` or `match()`.
- ✅ New tables `passages`, `passage_glosses`, `passage_topics`, `passage_attempts` (migration `c5a81f3e74b2`). Passage rows are updated in place on a content edit so attempt history survives; glosses rebuilt by DELETE-then-INSERT to avoid the `(passage_id, position)` collision.
- ✅ An exchange is laid out as `Question:` / `Answer:` on separate lines, in both the Norwegian and the reference translation. Structure is authored (`lines` on the passage), not inferred from question marks — `a1-023` is two question marks from one speaker.
- ✅ Translated passages count toward the dashboard's CEFR reading ring, as `MARK_CREDIT`-weighted sentence-equivalents added to the library's `sentences_read`. Before this the ring read "no reading yet" with 28 passages done, because it counted only the library.
- ✅ Running score (`Tally`): a percentage over attempted passages, with got/partly/missed counts, shown in the exercise header, on the A1 card in the Reading index, and in the dashboard's Reading subtitle. Latest mark per passage wins; a partial counts as a half; null before anything is marked so the UI says "not started" rather than 0%. The library half stays unscored.
- ✅ Queue: up to 3 passages that went badly lead a session, then the ones needing the fewest words explained (measured per learner from `user_glossary`), authored sequence as tie-break. No FSRS — a passage has no recall interval worth modelling. See ADR-041.
- ✅ **200 passages** hand-authored and gated (mean 92% deck coverage, every unknown word glossed in the form used, banned-word list, length bounds, role checks on exchanges), in `data/a1_passages_nb.jsonl`. 45 are exchanges, 47 are multi-sentence paragraphs; 6–53 words, median 11; 627 authored glosses. Michael confirmed the level on 2026-10-11 after working through the first dozen. **Target met.**
- Found on the way: the deck-coverage gate was blind to the 548 multi-word deck entries, because `FormIndex` sends those to `.phrases` and not `.forms`. Fixed in the gate; `FormIndex` itself is correct for its own job.
- Next: nothing required. Further passages (A2, or more A1 breadth) could be generated with Haiku 4.5 through the Batch API (under $1 for several hundred) rather than by hand, keeping the gates and the review step. The NorMistral second-pass grammar check needs its proposal diff fixed before its false-alarm rate can be trusted — it currently counts a verbatim quote of the original as a proposed change.

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

## ✅ M3.2 — Hear one word: yours against the reference, cut from the sentence (2026-10-17)

Michael: *"When I don't get a sound or word, I'd like to have the ability to play the word against my attempt so I can compare... both simultaneous playing panned hard left and right... and also to play each word separately. The words should come from the actual sentence audio used in the reference."*

- ✅ Tap a word in the feedback panel: **Reference**, **You**, or **Both** (reference left ear, you right). Each is cut from a whole clip — the reference at the speed on screen, and the take just heard — never synthesised on its own. Supersedes the per-word-clip plan of 2026-10-05.
- ✅ Whisper's word timestamps measured and rejected (boundaries mid-word, trailing silence glued to the previous word). Forced alignment instead: `NbAiLab/nb-wav2vec2-300m-bokmaal`, a hand-written CTC Viterbi plus energy-based boundary refinement, validated against the measured silences in four clips. `POST /align` on the speech service.
- ✅ `clip_alignments` caches reference placements by object key (content, shared by everyone, computed on first ask); `user_recordings.transcript_words` keeps the take's. Migration `f3c1a0d9e2b7`.
- ✅ Your side plays the word the model actually heard there, `klokke` for `klokka`; a word it found nothing for plays the stretch between its neighbours. The aligner being down costs the compare and nothing else. See ADR-043.
- Next: let the compare loop (play both, say it, play both); show the two waveforms; a per-word tally of which words he asks to hear, as a second signal beside the transcript tally.

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
   medium, but large is a real jump; *tried 2026-10-24, below*); scoring the audio's *likelihood against the known
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

   **`nb-whisper-large`, measured 2026-10-24 on every saved take — not worth the
   switch.** 569 takes (506 sentences) from 2026-10-04 to 10-22, run through
   `speech_service/transcriber.py` and scored with `app/pronunciation.compare`;
   `scripts/bench_stt.py` reproduces it. The harness exercises the production path: a
   fresh `medium` run matched all 547 transcripts the app had stored. "Gist" is a
   sentence with at least 80% of its target words found exactly or nearly — the
   measure the conversation feature cares about.

   | sentences | exact | words found | + near misses | gist | median / p90 per take |
   |---|---|---|---|---|---|
   | `medium` | 34% | 66% | 71% | 56% | 0.92 s / 1.10 s |
   | `large` | 37% | 68% | 73% | 60% | 1.46 s / 1.78 s |

   The gist gain is real but small (`large` better on 54 sentences, `medium` on 32;
   sign test p = 0.02); the exact-match gain is not (58 against 39, p = 0.07). On the 63
   single headwords `large` is worse, 43% exact against 48%. It is 1.6× slower on
   Metal and twice the weight to host. The two models trade errors rather than one
   dominating: `large` turns *"Kandar hjalp meg veldig"* back into *Kan du hjelpe meg,
   vær så snill?*, and makes *"At en frykt gjenoppretter henne"* of *Det er fire stoler
   her*. **168 of the 506 sentences lose their gist on both** — that third is out of
   reach of model size. `medium` stays. The 34% exact figure is below the 49% of
   2026-10-06, which was 43 sentences against today's 506; it is not drift in the
   pipeline, since today's `medium` reproduces every transcript it stored at the time.
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
5. ✅ **Conversation last (v1 built 2026-10-24, ADR-051).** Six graded scripted dialogues, a messaging-style screen, answers judged by per-letter CTC against each line's expected answers, every turn kept with its confirmed answer as corpus (`scripts/export_conversations.py`). Next: a week of real use, then export and re-tune `MARGIN`/`CLEAR` on replies. Original plan: Turn-by-turn: record → NB-Whisper → constrained character with a
   couple of tools → reply text → Piper audio. The most work, and steps 1–2 build most of
   what it needs. Not an open-ended agent (ADR-026). The 2026-10-24 benchmark (step 2)
   says a bigger recogniser will not carry it — a third of sentences lose their gist on
   both `medium` and `large` — so the turn has to absorb mishearings instead: show the
   transcript before the character replies, with retry or type-a-fix; tell the
   character it is talking to an A1 learner through a recogniser that mishears; and
   surface decoder confidence (`confidence` is `None` today, because the HF pipeline
   hides logprobs) so a doubtful turn gets *"Unnskyld, kan du si det igjen?"* rather
   than an answer to something never said. An audio-native model that hears meaning
   directly is the only real alternative, worth a bake-off on this corpus once the
   feature exists.

   **Context, measured 2026-10-24 — the turn's expected answers are the big lever, and the
   answers have to be known before the learner speaks.** A turn knows roughly what the
   learner will say, so `scripts/bench_context.py` simulated that on the same 569 takes.
   Each take got an answer set — its true target plus alternatives — and every candidate
   was scored by how well the audio supports it: log P(text | audio) under the aligner's
   `nb-wav2vec2` CTC model, **per letter** (the raw sum favours shorter texts: the target
   beat Whisper's transcript 86 times when it was the shorter text and 55 when longer),
   ~0.16 s a take for all candidates. The rule: take the best expected answer unless
   Whisper's own transcript beats it by more than a margin. "Off-script" removes the target
   and asks how often a wrong expected answer is accepted anyway. Sentences, 506; free
   decoding gets 34% exact, 56% gist:

   | answer set: target + … | set alone | with transcript, margin 0.3 / 0.5 nats per letter | wrong answer taken off-script |
   |---|---|---|---|
   | 4 nearest deck sentences | 95% | 81% / 88% | 11% / 12% |
   | 7 Haiku-written alternatives (question + target shown) | 96% | 82% / 89% | 10% / 12% |
   | 4 one-word near-spellings (*hjelpe / hjalp*) | 83% | 72% / 78% | 44% / 56% |

   - **Distinct answers make recognition work.** 34% exact becomes ~85% when the
     alternatives differ by more than a word, and an LLM writes sets as separable as
     hand-picked ones — so the sets need not be authored.
   - **Near-spellings defeat it.** One similar-sounding word apart, the audio cannot tell
     the candidates apart reliably — and sometimes that is because the learner did say the
     other word, a pronunciation fact the turn should report, not paper over.
   - **The answer cannot be anticipated from the question alone.** Haiku's eight likeliest
     A1 answers to the question contained what the learner actually said 4% of the time
     exactly, 15% by gist. (Pessimistic — the questions were invented from deck sentences,
     which are odd answers to anything — but the direction is clear.) So the set has to
     come from the turn's design, not from guessing.
   - **Proposing after hearing is a modest gain, not a fix.** Given the question and
     Whisper's misheard transcript, Haiku's single best guess reaches 40% exact / 57% gist;
     its eight candidates contain the target 59% of the time; letting CTC choose among them
     plus the transcript gives **44% exact / 65% gist**, while asserting a repaired sentence
     that is wrong 19% of the time. The candidates are all phonetic neighbours of the
     transcript, which is the near-spelling case again.
   - **Whisper's n-best adds nothing** (target present 37%; CTC choosing among the beams
     33% exact against greedy's 34%; transformers 5.18's Whisper `generate` returns copies
     of the top beam — the script goes through `GenerationMixin.generate`). **wav2vec2's own
     transcript is unusable** as text (4% exact, 23% gist): it stays the acoustic judge, not
     the scribe, and Whisper stays the pronunciation record. A GOP-style threshold against
     the unconstrained CTC path did worse than comparing against the transcript.

   So the turn design follows. **Shape turns so the answer space is small and known** —
   closed questions, offered choices, slots whose values the app already holds (the
   learner's town, the menu it just showed) — which is ADR-026's constrained character
   anyway; have the LLM write the alternatives when it writes the question, requiring them
   to differ by more than one similar-sounding word; run Whisper once; CTC-score the set
   and the transcript per letter, margin 0.3–0.5 nats per letter. **Open turns get the
   post-hoc proposal** (question + transcript → candidates → CTC), knowing it lifts exact
   from a third to under a half and must not act silently. Confirmation is gated on the
   acoustic margin: a clear win is echoed in the character's reply (*"I Oslo! Hyggelig."*);
   a narrow one becomes a choice (*"Sa du Oslo eller Molde?"*), which also teaches. Keep
   the unassisted transcript for pronunciation feedback, and log audio, candidates, scores
   and the pick: that is the real conversation corpus, which does not exist yet — these
   takes are sentences read after a reference, not replies.

   **Build plan for the first conversation: `docs/plans/conversation-corpus.md`**
   (2026-10-24) — scripted dialogues as content, pre-generated Piper lines, CTC-scored
   expected answers, confirmation banding, every turn logged as corpus. No LLM in the
   request path in v1.

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

### `start.sh` reported a wedged port as a slow start (fixed 2026-10-12)

Michael: *"why is start.sh taking so long to start up the application... took several minutes bringing up the back end?"*

The backend was never slow. Measured on his machine: Alembic 0.04s, `seed_data` 0.79s, `backfill_glossary` 2.04s, whole app startup 3.7s, and a cold `uv run uvicorn --reload` answering in 4.3s. Local Postgres round-trip is 0.23ms, which is why `backfill_glossary`'s ~6,000 round trips cost two seconds here and ~75s in production (see the cold-start entry).

The fault was in `wait_for`, which counted loop iterations as if each took a second:

```bash
while [ "$waited" -lt "$secs" ]; do
  up "$url" && return 0      # curl --max-time 2
  sleep 1
  waited=$((waited + 1))
done
```

Each iteration costs up to 3s, so `wait_for "$API_URL" 60` allowed **up to 3m15s, measured**. The overrun is worst in exactly the case worth diagnosing: a port held by a dead worker accepts the connection into its backlog and never answers, so every probe burns the full curl timeout. A port with nothing listening refuses instantly and the loop runs at its nominal speed — which is why this never showed up until a copy of the API was left holding :8000.

Fixed by taking a real deadline from bash's `SECONDS` (10s asked now waits 12.4s, bounded by one final probe, rather than 32.4s). Three things came with it: the log is appended to with a timestamped banner rather than truncated, because two concurrent runs overwrote the one line that said `[Errno 48] Address already in use`; a `diagnose` helper runs after a failed wait and names that error specifically, since the pre-flight `wedged` check cannot see a corpse that was still exiting when it looked; and both start paths now print how long they took.


- **Cold-start timeout in production (diagnosed 2026-10-11, fixed 2026-10-19 — ADR-047).** Michael hit `Request failed (500)` on the live app; a retry a minute later worked. The app is not down — when warm, health and every dashboard endpoint answer 200, Neon is healthy (never suspends, 32 MB of a 1 GB limit) and R2's deck is untouched since 1 Oct. It is a cold boot that has grown too slow to answer in time.

  The container has `sleepAfter = '15m'` and there is one user, so most visits are cold. `lifespan` runs migrations → `seed_data()` → `backfill_glossary()` before uvicorn serves anything. Timed locally: imports 0.82s, alembic 0.05s, seed 0.24s, **`backfill_glossary` 2.19s**. That last one is the fault. Its own docstring calls it *"one-off: build glossary entries from review history predating the feature"*, but it runs on every boot, looping over every card with review history and issuing `session.get` per candidate word — **6,281 DB round trips, about 28 per reviewed card**, every one of them a no-op now that the 211 glossary rows exist.

  Production has 151 reviewed cards, so roughly **4,200 round trips per boot**. Locally that costs 2s because Postgres is on localhost; against Neon in Sydney it costs 4,200 × the round-trip time. Measured 18 ms from Michael's Mac to the Neon host — order-of-magnitude only, since the container's latency is its own — which lands near **75s**; even an optimistic 5 ms gives ~21s. **The cost scales with use**: 151 reviewed cards now against fewer at the 4 Oct deploy, which is why this only started biting.

  The diagnosis is also what the error message says: the UI prints the server's `detail` when there is one, and FastAPI's own 500 would read "Internal Server Error". `Request failed (500)` is what it prints when the body is not JSON at all — consistent with the container not yet being ready.

  **2026-10-19:** by now every cold visit failed outright (*"The container is not listening in the TCP address 10.0.0.1:8000"* after ~50 s), and Neon had archived the idle branch on 2026-10-15, adding a restore to every boot. Fixed per ADR-047: (1) is done, `backfill_glossary` records a marker in `app_meta` and later boots skip it; and the server now listens before touching the database, preparing it in the background with retries while API requests wait (up to 25 s, then 503 with `Retry-After`). That covers the server side of (3); the worker's bare `fetch` is unchanged but no longer sees a dead container. (2) is still open.

  Fixes, in order of value for effort: (1) stop running `backfill_glossary` on every boot — gate it on a flag in `app_meta` or move it to a script; it finished weeks ago and removes most of the boot cost for a few lines. (2) Move migrations and seeding out of startup into a deploy step — already the stated intention in the AWS-era plan, never carried over to Cloudflare. (3) `worker/index.ts` does a bare `getContainer(env.BACKEND).fetch(request)` with no retry or error handling, so a boot in progress surfaces as a raw 500; retrying, or returning something the UI can render as "waking up", turns an error into a wait.

- **Swallowed opening word (diagnosed 2026-10-04, fix deferred).** Some clips sound cut off at the start. **Not truncation and not playback** — Piper's `nvcc` pool voices under-articulate the first word, so it is mumbled rather than missing (ADR-028). 67 of 766 example clips affected, recorded in `data/audio_onset_issues.json`; `nvcc:4` accounts for a third of its own clips while `talesyntese` and `nvcc:6` are clean. `scripts/check_audio_onsets.py` now gates new content so the list cannot grow unnoticed. Fixing the existing 67 means regenerating them on another voice — deliberately deferred, since the voice is part of the content hash and re-picking reshuffles the corpus.

- **Mysterier: 33 source-side findings left (2026-10-05).** The deterministic gates (ADR-029) repaired all 7 translation-side faults, and what remains needs a human: 28 `norwegian_left` (a mix of genuinely untranslated sentences, place names, and one hallucinated place — Vardalskogene became "the forests of Vardsø") and 5 `digit_mismatch` where a print page number is fused inside a word (`jeg36var`) and so the glued-number check cannot see it. Inspect with `uv run python -m scripts.check_translation data/texts_staging/mysterier.json --show norwegian_left`. The fused-digit case wants an extraction fix in `prepare_text.py`, not a retranslation.

- **Folktale titles are hand-corrected, not derived (2026-10-05).** The 25 tales imported on 2026-10-05 have English titles set by hand where gemma3 was wrong, which was about half of them (ADR-030). They live only in the database: `scripts/retitle.py --list` shows them, and `import_text` preserves them on re-import. Two are deliberately null — `Fykomfei` and `Ringelihorn` are proper names, so the list shows the Norwegian alone. Worth a second opinion on the Nordland-dialect ones (`Oterhammen` → "The Otter Skin", `Jomfru Rosenving på Santavajasø` → "The Maiden Rosenving of Santavajasø").

## Proposed / unscheduled

- Real auth provider (replaces dev user store).
- Additional languages (schema is language-agnostic from M1).
- Streaks and gamification; progress dashboard v2.
- shadcn/ui + TanStack Query adoption in frontend as UI complexity grows.
