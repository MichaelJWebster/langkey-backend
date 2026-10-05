# LangKey Architecture Decisions

Lightweight ADR log. Newest first. Each entry: decision, rationale, revisit-when.

Scope is the whole of LangKey, not just this repo: decisions about the frontend
and about deployment live here too. It is kept in the backend repo because the
umbrella directory (`~/work/lang_app`) is not version-controlled, and symlinked
back to there so the familiar path still works.

## ADR-030 (2026-10-05) — Reading library: categories derived from `kind`, English titles stored and hand-correctable

The reading library opens on broad categories rather than a flat list. A category *is* a text's `kind` (set by `prepare_text --kind`), grouped client-side in `categories.ts` from the listing the API already returns. There is deliberately no category table and no new endpoint: a new kind becomes a new category the moment the first text using it is imported, with a label derived from the kind string, so adding one costs an import and no code. `KNOWN` in that module only overrides labels and ordering for the kinds worth phrasing well; anything unfamiliar sorts to the end with a humanised label rather than being dropped. Revisit: if a text ever needs to appear in two categories, or if categories need their own ordering/description editable outside the code — then this wants a table.

Texts carry `title_en` beside `title`, nullable. It is translated at prepare time and checked by the ADR-029 gates, plus two rules that only make sense for titles: a reply more than three times the title's length is the model explaining itself, and a multi-word title handed back unchanged is the model giving up (a *one*-word identical reply is kept, because Victoria and Pan are titles in both languages). **Null is a first-class answer** — the reading list then shows the Norwegian title alone, which is what it would have shown anyway, so dropping a doubtful title costs nothing while storing a wrong one misleads the one decision a learner makes before reading.

Measured on the 27 texts in the library, machine-translated titles are much weaker than machine-translated prose: roughly half needed correcting, and the failures are semantic, so no gate can catch them (`Kveita` → "The Wife" for *the halibut*; `Havmannens sønn` → "The Mermaid's Son" for *the merman's*; `Tredokka` → "The Busybody" for *the wooden doll*). Four words carry no context, and unlike a sentence a title has no Norwegian beside it in the list to correct the impression. So `scripts/retitle.py` exists to set one by hand, and `import_text` keeps an existing `title_en` when the staging file has none — a hand correction lives only in the database and must survive a re-import.

Revisit: if a better translator (Claude, per ADR-021's `translator` column) makes title translation reliable enough to trust unreviewed.

## ADR-029 (2026-10-05) — Translation QA: deterministic gates, with repair wired only to gates measured near-perfect

Translations are checked by `scripts/translation_gates.py`: pure functions over the staging JSON, no model involved. Each gate is classified by who can fix what it finds. **TRANSLATION** means the Norwegian is sound and the English is wrong, so the translator can be asked again; **SOURCE** means the Norwegian itself is corrupt and retranslating cannot help, so the finding is reported and the fix goes upstream into extraction. `scripts/check_translation.py --repair` sends only the repairable findings back to the model with an instruction naming the specific fault.

The decision that matters is which gates get an instruction at all. **A gate with repair enabled rewrites what it flags, so a false positive does not waste a review — it replaces a correct translation with a worse one.** Measured on Mysterier (7228 sentences): the "Norwegian left in the English" check fires 28 times, but place names and deliberately kept loanwords land there too, and repairing those turned `ten øre` into `ten pence` and `«Skål!»` into `Cheers!`. It is report-only for that reason, and so is every other gate whose precision was not near 100%. Each gate carries its measurement in a `measured` field, and a test asserts the field is filled — the rule is enforced in code, not remembered.

A repair is accepted only when the gate that fired now passes **and** no gate that was passing has started failing, compared against the faults the sentence already had. Source-side faults are excluded from that comparison, or a page number fused into the Norwegian would make the sentence permanently unrepairable. Two attempts, then it is left for a human. Every accepted repair is recorded on the sentence as `repairs` (before/after), so a review can always see what the model first produced.

The acceptance rule caught its own hole in the first real run: the model answered one repair with the Norwegian sentence untouched, and no gate could see it — the word counts matched, and that sentence happens to contain no æ, ø or å. The echo check that already existed inside the translation loop became the `echoed_source` gate, with a four-English-word floor (without it, it fires 80 times on short lines that are their own translation: `Pause.`, `He-he-he.`).

`scripts/import_text.py` refuses a file with unresolved **fixable** findings and prints the `--repair` command; `--force` overrides. Source-side findings only warn. A gate that forces `--force` on every import teaches you to ignore it.

Revisit: when a second book's corpus disagrees with these measurements, or when a semantic check (does the English mean what the Norwegian means) is added — that needs a model and belongs beside these gates, not instead of them.

## ADR-028 (2026-10-04) — Audio QA: gate new clips on a swallowed opening word

Piper's multi-speaker `nvcc` voices under-articulate the first word of a sentence often enough to notice: measured over 766 example clips, **8.7% open too quietly**, with `nvcc:4` at **33.8%** (23 of 68) while **`talesyntese` and `nvcc:6` are at 0%**. The controlled case is 131 sentences that all begin "Jeg": median onset strength 83.8%, the strongest all `talesyntese` at 100%, the weakest all pool voices at 40–55%. The same word in the same prosodic position reaching half the level is a voice-quality difference, not prosody. The audio is **not** truncated — the word is mumbled — so leading silence and file length cannot detect it (the clip that prompted this has 360ms of lead-in).

Metric: the loudest 20ms within 200ms of speech onset, over the clip's own peak; flagged below 0.60. Two approaches were tried and rejected first — isolating the first word by its energy burst stops at the dip inside fricative-initial words like "smør" and "Jeg" and reports them as swallowed, and bridging those dips runs on through word boundaries, which in connected speech are frequently shorter, and measures the whole sentence. So no segmentation: a fixed window from onset. Headwords are excluded because many are an unstressed article or infinitive marker before a stressed word ("et hus", "å ville"), where a quiet opening is correct Norwegian.

`scripts/check_audio_onsets.py` is a **gate, not a repair**: it exits 1 on any weak clip not already in `data/audio_onset_issues.json`, so new content cannot quietly add more, and `--update` re-records the list. The 67 existing clips are recorded as a to-do; they are deliberately left alone rather than re-voiced, at the user's instruction, because re-picking voices changes the content hash and would regenerate most of the corpus. Workflow after content edits is now: `publish_deck` → `tts` run → `check_audio_onsets`. Revisit: fixing the recorded 67; `nvcc:4` is a candidate for removal from the pool (ADR-018 anticipated pruning poor speakers), though that reshuffles every hash-picked assignment.

## ADR-027 (2026-10-04) — Standing principle: design for many users, deploy for one

Architecture is designed on the assumption that LangKey may be commercialised and have to serve very many users; deployment is sized for the single-user prototype. The two are decided separately and argued separately, and every ADR should be clear about which layer it is talking about. **Deployment settings are cheap to change and may stay minimal**: container instance type and `max_instances`, scale-to-zero, one Neon branch, models running on the developer's Mac, a single region. **Code and schema must not assume one user**: no per-user state in process memory (any component must be able to run as more than one replica), no missing `user_id`, no request-path work that belongs in a queue, no local disk as a store of record, no N+1 query that only survives because one person is using the app. Audited 2026-10-04. Properties already held, to preserve: `user_id` on every user-owned table (indexed, with the right uniqueness constraints) and none on shared content tables; no per-user state in process memory (the only cache is `lru_cache` on immutable settings), so the API is already replica-safe at request level; no local disk as a store of record (content-addressed audio in R2/MinIO, everything else in Postgres); immutable append-only event logs (`review_events`, `exercise_attempts`); audio delivery behind a module seam (`app/audio.py`). **One door is currently closed:** `lifespan` in `app/main.py` runs Alembic migrations, `seed_data()` and `backfill_glossary()` on every container boot — correct at `max_instances: 1`, but two or more replicas would race on the same database. Trigger to fix before raising `max_instances`: move schema/seed work to a deploy step or guard it with a Postgres advisory lock. **Two seams do not exist yet and must be built open rather than retrofitted:** exercise generation is inline in `app/routes/exercises.py`, and there is no STT boundary at all, which is why speech must arrive as its own service rather than as code inside the FastAPI container (ADR-024, ADR-025). Deliberately *not* built until something demands them: queues, autoscaling, read replicas, sharding, multi-region, a CDN tier beyond what Workers already gives. The requirement is that the design permits them cheaply, not that they exist; where one is deferred, the ADR names the trigger and the cost rather than the mechanism. Revisit: at the first sign of a second concurrent user, or any commercialisation decision.

## ADR-026 (2026-10-04) — Agents belong in the content pipeline, never in the learner's request path

Agentic workflows (a model that plans, calls tools, checks its own work, and loops) are adopted only for offline content work behind the existing human review gate: filling the missing example sentences for items 151+, drafting answer alternatives so typed cards accept more wordings, and building the parked mock-Norskprøve exercises. The pattern copies ADR-021's text pipeline — pick the work, model drafts, automatic checks (known words only? can Piper say it?), staging folder, Michael approves, then the database. The gate is the design: everything before it can be wrong at no cost. Explicitly excluded: (a) anything the learner waits on — marking an answer, scheduling a card — which must stay fast and deterministic (ADR-019, ADR-022), and (b) the conversation partner, which is better modelled as a tightly constrained character with two or three tools than as an agent free to decide things. For a one-user app "agentic" should mean a script that calls a model and leaves something to review; queues, retries and monitoring are infrastructure with no payer here. Revisit: if content volume ever outgrows one person's review capacity.

## ADR-025 (2026-10-04) — Pronunciation assessment: not Kaldi; start bought, keep the option to self-host

Pronunciation scoring is a separate feature from conversation (ADR-024) with the opposite requirement: it must keep the learner's errors rather than repair them, so a speech-to-text model cannot do it — Whisper's value is precisely that it tidies wobbly speech into correct Norwegian. The pipeline is four steps: record → align the audio to the known target text → score each sound → explain. Kaldi, though the classic GOP (Goodness of Pronunciation) toolkit, is rejected: C++ with a steep build, a dying contributor base, no ready Norwegian acoustic model, and — decisively — NOCASA 2025 (Norwegian L2 pronunciation challenge) was won by wav2vec2/XLS-R CTC-GOP systems, not Kaldi. Chosen path: prove the feature with Azure Pronunciation Assessment (supports `nb-NO`, returns per-phoneme scores, pay-per-use), and only then decide whether to self-host a wav2vec2 CTC-GOP stack with NoFA (Språkbanken's Bokmål forced aligner) and the NST pronunciation lexicon. Always scoped to a known target sentence — never free speech. Expectation-setting: NOCASA's best system reached 36.37% UAR on four-way severity rating, so per-phoneme verdicts are a hint, not a grade; the UI must present them as "worth another go", and recording + A/B playback against the Piper reference carries much of the learning value on its own. Revisit: when there is a corpus of Michael's own recordings to measure against; if Azure's Norwegian scores feel wrong, self-hosting moves up rather than the feature being dropped.

## ADR-024 (2026-10-04) — Conversational speech: NB-Whisper, turn-by-turn, not on the Cloudflare container

Speech-to-text for the conversation feature uses NB-Whisper (National Library of Norway, Whisper retrained on 66,000 hours of Norwegian, Apache 2.0). The margin decides it: WER 6.6 vs plain Whisper large-v3's 10.4 on Fleurs, 2.2 vs 6.8 on NST. Alternatives are worse for Norwegian — plain Whisper gives up that margin, and commercial services treat Bokmål as an afterthought. Interaction is turn-by-turn (press, speak, release, transcribe) rather than streaming: simpler, and it matches how a learner actually composes a sentence. No GPU is required: utterances are seconds long, and faster-whisper's published CPU benchmark (small/int8, 8 threads) transcribes 13 minutes of audio in 1m42s using 1.5 GB RAM — several times faster than real time. A GPU buys latency on `large` and concurrency, neither of which a one-user app needs. Deployment, in build order: the M2 Max via Metal during development (free); then the existing Cloudflare container resized — `basic` (¼ vCPU, 1 GiB) is far too small, but the tier ladder runs to `standard-4` (4 vCPU, 12 GiB, 20 GB disk), which fits `small`/`medium` comfortably and `large` slowly, with the weights baked into the image (image cap = instance disk) so a scale-to-zero cold start does not pull from Hugging Face; Workers AI `whisper-large-v3-turbo` at $0.0005/audio-minute remains the zero-infrastructure fallback, but it is generic Whisper and gives up the Norwegian margin. Dedicated GPU hosting (EC2/ECS, or a scale-to-zero provider) is deferred until something demands it — an always-on g4dn.xlarge in Sydney is ~$500/month to serve one learner. ONNX and ggml/whisper.cpp builds exist; there is no official CTranslate2 conversion, so faster-whisper (the M3 plan of record) would need converting first. Revisit: when Workers AI or Cloudflare offers a Norwegian-tuned model, or when the local prototype justifies paid hosting.

## ADR-023 (2026-10-02) — Aurora skills dashboard: CEFR skills as the app's information architecture

The home screen is organised around five CEFR skills (vocabulary, listening, reading, writing, speaking) rather than activity buttons, in the "Aurora dark" visual direction Michael picked from five interactive wireframes (Design canvas artifact). Each skill row shows a gradient progress ring on a curved journey line (progress toward the next CEFR band from `/progress/cefr`), the concrete numbers beneath it, and a milestone box with that skill's next-level can-do text; speaking is disabled until M3. Practice always runs under a persistent "← Skills" header so sessions can be abandoned at any point. One responsive codebase: rows restack at the `md:` breakpoint (ring moves into the card, milestone collapses to `<details>`). Supporting API: `/api/v1/progress/skills` (per-skill state breakdowns for the graphics), `topics[].established`, review queue `direction` filter (writing = production-only decks) and `mode=tricky` (seen-but-unestablished cards, highest stability first — nearly-learned words repeat before barely-met ones), default deck size 15 (user request, was 30). Revisit: focus persistence across sessions; A2+ can-do texts are stubs beyond A1.

## ADR-022 (2026-10-02) — Graded exercise marks and CEFR estimation

Listening attempts carry a `score` 0..1 (share of the sentence produced unaided; Levenshtein partial credit per misspelled word with a 50%-similarity floor; full reveal = 0). The mark drives dictation speed pacing (mean of last 12) and weighted sentence resampling (`_practice_need`: up to 4× likelihood for recently flubbed sentences, Efraimidis–Spirakis sampling). `/api/v1/progress/cefr` estimates per-skill CEFR position from an "effective vocabulary" read against Milton & Alexiou X-Lex sizes (A1≈500 … C2≈9000 lemmas); overall level = weakest practised skill; explicitly an estimate, not certification (Norskprøven is the official test). Pre-grading rows fall back to the old binary reading (hint-free correct = 1). Revisit: mock Norskprøve task sets (formats may be modelled from prove.hkdir.no samples; their content is copyrighted — original texts only).

## ADR-021 (2026-09-29) — Reading library: offline translation pipeline, sentence-per-row storage

Interleaved NO/EN reading texts come from an external pipeline, never the request path: `scripts/find_texts.py` (bokselskap.no catalogue / Gutenberg search) → `scripts/prepare_text.py` (fetch, class="para"-based extraction, sentence segmentation, local-LLM translation via Ollama — default gemma3:12b, paragraph-batched for context with numbered-line alignment, echo-detection retry for untranslated replies) → staging JSON in `data/texts_staging/` (gitignored; human review point) → `scripts/import_text.py` into Postgres. Storage is one row per sentence (`texts` / `text_sections` / `sentences` / `sentence_translations`): sentences immutable after import, translations versioned per translator with a partial unique index enforcing one current row, so gemma drafts can be upgraded (Claude/human) per sentence without re-import. `/api/v1/texts` serves 100-sentence pages; `user_reading_positions` (forward-only) is both resume point and cursor. Benchmarked first (Askeladden + modern Wikipedia passage): dedicated MT (OPUS-MT/NLLB-600M) unusable on 1904 orthography and drops facts on modern text; gemma3:12b good and literal; Claude best on rare/archaic vocabulary. Ollama models live on /Volumes/nvm_2tb. Revisit: per-sentence Piper audio (audio_key column ready) to link reading with listening; tap-a-word glossary; Claude repair pass for suspect sentences.

## ADR-020 (2026-09-28) — Adaptive listening difficulty

Three levers adapt to the learner: (1) dictation speed ladder — every example sentence has primary-voice clips at length_scale 2.0/1.6/1.25; the server suggests one from the last 12 dictation attempts (hint-free accuracy <55% → very slow, <80% → slow, else normal) and the UI offers override chips, with the used speed logged on each attempt; (2) sentence ease — dictation candidates are ranked by known-word coverage (glossary form-index matches ∩ items the user has met, over sentence length) and sampled from the easiest 60%; (3) exercise mix — dictation share of a session grows with total attempts (25% < 25 attempts, 40% < 75, then 60%). Piper note: durations are stochastic (noise_w), so scale verification needs repeated samples; an earlier generation mislabelled 1.5/1.8 clips a level fast, fixed by widening the ladder and regenerating.

## ADR-019 (2026-09-27) — Listening exercises check client-side, log server-side

Dictation targets travel to the client (practice, not an exam) so checking is instant: normalised comparison, with a diacritic-folded pass marking å/ø/æ slips as "nearly right", never wrong (US-keyboard friendliness; on-screen æøå buttons double as ⌥-shortcut teachers). Every attempt lands in `exercise_attempts` (immutable, like review_events) for future analytics/personalisation. Exercises draw only from items the user has met (recognition card reviewed). Revisit: exercise results could feed FSRS scheduling as review evidence; server-side checking if anything competitive ever appears.

## ADR-018 (2026-09-27) — Audio: 80% speed, multi-voice example sentences

All clips generated at Piper `length_scale=1.25` (≈80% speed, natural slow speech — not playback stretching). Headwords use the primary voice (talesyntese); each example sentence gets a stable hash-picked voice from an 11-voice pool (talesyntese + `no_NO-nvcc-medium`'s 10 speakers) so the learner hears varied speakers. Voice + scale are inside the content hash, so parameter changes regenerate cleanly; the generator prunes clips no longer referenced. Revisit: prune bad nvcc speakers from the pool if any sound poor; per-user speed setting.

## ADR-017 (2026-09-27) — Per-user glossary with example-sentence encounters

`user_glossary` records every deck item a user has met: the card's own word on first review ("card"), plus deck words recognised inside its example sentences ("example") via heuristic Norwegian morphology matching (`app/glossary.py`: suffix expansion + irregular-verb table + phrase substrings; misses acceptable — it's a reference, not the scheduler). Populated on first review of a card; backfilled at startup from review history. `/api/v1/glossary` reports each word's status (established / learning / seen-in-examples-only). Revisit: proper lemmatiser if matching quality matters later; tap-a-word inline glosses in the review UI.

## ADR-016 (2026-09-27) — Topics as new-card focus, not separate decks

Items carry a `topic` slug (19 areas; labels in `app/content.py`). A chosen focus steers only which NEW cards are introduced (`/reviews/queue?focus=...` — focused items first, merged across existing-unseen rows and uncarded items, spilling to frequency order when the topic runs out). Due reviews stay a single global FSRS queue — themed sub-decks would break scheduling. UI: dashboard focus chips (persisted in localStorage) + session-end "Keep going"/"Change focus". Richer themes (dialogues, lesson packs) belong to the exercises/content milestones, not the flashcard flow. Starter themed phrases added: meeting-people, directions, shopping (8 each).

## ADR-015 (2026-09-27) — Single learning step: no in-session repeats except Again

User found sessions too long (they shadow every phrase aloud, so each card takes real time). Learning steps cut from FSRS defaults (1m + 10m) to a single 1m step, relearning to a single 5m step: Good on a new card graduates immediately (returns in ~2–3 days); only Again cycles a card back within the sitting. Pedagogically sound: within-session repeats are massed practice, the weakest spacing; between-day gaps (untouched) carry the retention, and shadowing makes first exposures deep. Revisit: if new-word retention feels weak, restore a second step or add a next-day cap.

## ADR-014 (2026-09-27) — Piper audio pipeline with content-addressed clips

Audio clips live in MinIO keyed `audio/<lang>/<sha256(text)[:20]>.wav` — derived from the spoken text itself (derivation duplicated in `app/audio.py` and `docker/piper/generate_audio.py`; keep in sync). No DB bookkeeping: content edits get new audio on the next `docker compose --profile tts run --rm tts` (idempotent; reads the published deck Parquet, generates only missing clips; Norwegian voice no_NO-talesyntese-medium baked into the image). The API streams clips at `GET /api/v1/audio/{lang}/{hash}.wav`, unauthenticated (course content, no user data; Audio() elements can't send bearer tokens) with immutable cache headers. Frontend plays clip URLs and falls back to browser TTS per-URL on 404. Revisit: OGG/opus transcoding if storage/bandwidth matters; auth if audio ever becomes user-specific.

## ADR-013 (2026-09-27) — TTS: browser speech synthesis first, Piper-ready

The speak button uses the browser's `speechSynthesis` with an `nb-NO` voice (rate 0.9). Items and examples carry nullable `audio_key` columns; `speech.ts` prefers a pre-generated audio URL when present, so the Piper→MinIO pipeline (M2) slots in without UI changes. Revisit: when M2 lands, decide whether browser TTS remains the fallback for devices without audio yet.

## ADR-012 (2026-09-27) — Content pipeline: repo source → Parquet in MinIO → Postgres

Human-editable sources stay in-repo (`data/seed_deck_nb.json` + `data/extra_examples_nb.json`, merged by key `<no>||<en>`). `scripts/publish_deck.py` merges and publishes `decks/<lang>.parquet` to MinIO via DuckDB (boto3 for bucket/upload — no runtime DuckDB extension downloads). API startup loads the MinIO snapshot (falls back to local files in tests / before first publish) and syncs Postgres: items upserted by (language, source_text, translation) so user_cards FKs survive; examples replaced wholesale; a content hash in app_meta skips no-op syncs. Postgres remains the operational store — DuckDB/MinIO is the content/asset layer, per the M4 lakehouse direction. Revisit: consolidate the two source files when the M4 LLM content pipeline restructures authoring.

## ADR-011 (2026-09-26) — No daily new-card cap; batches per session

User decision: practice must be available whenever they log in. New cards arrive in batches of `NEW_CARDS_PER_SESSION` (10) per queue fetch, with no daily limit — each finished session unlocks another batch. Trade-off accepted: heavy binge days create larger future review load than a capped drip would (FSRS still spaces each card correctly). `new_available` in /progress reports total unseen candidates. Revisit: if review debt from binge sessions becomes unpleasant, add an optional soft cap or a "review-only" mode toggle.

## ADR-010 (2026-09-26) — Staged card directions

Every item first gets a recognition card (NO→EN). A production card (EN→NO) becomes eligible for introduction only after the item's recognition card graduates to FSRS Review state. Balances early speaking-oriented production practice against day-one review load. Revisit: if production cards feel redundant once speaking exercises (M3) exist.

## ADR-009 (2026-09-26) — FSRS via py-fsrs, state stored on user_cards

`user_cards` rows mirror py-fsrs Card fields (state/step/stability/difficulty/due); `app/scheduling.py` is the only bridge between the library and the DB. Default parameters, 90% retention; new-card batch size is a constant in scheduling.py (see ADR-011). `review_events` is an immutable append-only log — never update it; it feeds progress, streaks, and M4 analytics/personalisation. Revisit: parameter optimisation once real review history accumulates.

## ADR-008 (2026-09-26) — Seed deck is Claude-authored JSON in-repo

`data/seed_deck_nb.json`: 447 curated A1–A2 entries, loaded idempotently at startup (only when the items table is empty for that language). Frequency rank = array order. Adding languages = adding a deck file + Language row. Revisit: M4 (LLM-generated content in-app), or if item edits need to propagate to an already-seeded DB (would need a proper content-sync command).

## ADR-007 (2026-09-26) — Postgres host port 5433 in dev

Compose maps Postgres to host port **5433** because another local container already uses 5432. Inside the compose network it remains 5432. Revisit: never, this is dev-only convenience.

## ADR-006 (2026-09-26) — DuckLake deferred to M4

Day-to-day data (users, items, reviews) is relational and transactional → Postgres. MinIO handles object storage (audio) from M2. DuckLake is added in M4 when analytical queries over review history exist; it layers over the same MinIO/S3 storage, so deferral is additive, not rework.

## ADR-005 (2026-09-26) — Frontend: React 19 + Vite + TypeScript + Tailwind v4

SPA (no SSR) since the app is login-gated with no SEO needs. shadcn/ui and TanStack Query planned when UI complexity grows (M1). Dev server proxies `/api` → backend :8000; production serves static files (nginx image).

## ADR-004 (2026-09-26) — Auth: dev-grade contents, production-grade shape

bcrypt password hashes in Postgres, JWT bearer tokens (PyJWT), `get_current_user` FastAPI dependency. Replacing with a real identity provider later touches only the login route; every protected endpoint keeps the same dependency contract. JWT secret is a dev default — must come from env in any deployment.

## ADR-003 (2026-09-26) — M0 schema via `create_all`; Alembic from M1

Startup `create_all` + idempotent seed is enough for the two-table M0 schema. Alembic is introduced in M1 with the learning-domain schema, baselining what exists. Revisit: M1 (planned).

## ADR-002 (2026-09-26) — Backend: FastAPI + SQLAlchemy 2 (async) + uv

FastAPI gives Pydantic validation and an auto-generated OpenAPI spec, which is the contract with the frontend repo. Async SQLAlchemy with asyncpg. Tests run on in-memory SQLite (aiosqlite) for speed; anything Postgres-specific must gain integration tests. `uv` for dependency management.

## ADR-001 (2026-09-26) — Two repos, versioned HTTP API between them

`langkey-backend` (deployable to a server) and `langkey-frontend` (browser SPA), coordinated from the `~/work/lang_app` umbrella directory (not a repo itself; holds ROADMAP.md, ARCHITECTURE.md, kickoff prompt). API is versioned under `/api/v1`. The exercise-generation layer must sit behind an interface so a rule-based generator can be swapped for an LLM-backed one (M4) without API changes.
