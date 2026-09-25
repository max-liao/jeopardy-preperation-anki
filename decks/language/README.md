# Foreign-language decks — flag-driven automation

Automation for the three language decks in the live Anki collection: **Français**, **Español**, **中文**. You flag cards in Anki; a script proposes the edit; you review; it applies and clears the flag.

> **Status: design approved 2026-09-25; docs and the repo restructure are done, no language code written yet.** This document is the spec and the decision log. It is updated as the design changes. Roadmap at the bottom.

Related: [`docs/ANKI_NOTES.md`](../../docs/ANKI_NOTES.md) (collection and AnkiConnect facts) · [`decks/trivia/`](../trivia/README.md) (Jeopardy and US Presidents, a different family with its own pipeline).

---

## 1. What the flags mean

Anki flag integers: 1 red, 2 orange, 3 green, 4 blue (stored in the low 3 bits of `cards.flags`).

| Flag | Meaning | Done by hand today | What the automation does |
| --- | --- | --- | --- |
| **Blue (4)** | Needs pronunciation | AwesomeTTS (Google Translate voice) reads the Back text; Chinese also gets a `(Pinyin)` line | Generates the same audio and, for Chinese, the pinyin line |
| **Green (3)** | Needs image(s) | Pick the best Google Images result by hand | Fetches candidates; **you pick** from a contact sheet; script inserts the image |
| **Orange (2)** | Needs context | Dictionary definition + dictionary example sentence; the example goes **in English on the Front** | Drafts definition + example from datasets/Larousse/Claude; you review |

**A card carries exactly one flag.** A card that needs audio *and* an image is handled one need per pass: flag blue, run, re-flag green, run. A successful run clears the flag.

Live state on 2026-09-25 (Anki closed, read-only inspection): 9 orange + 1 green cards, all in 中文; **zero** blue. The one green card (掌握) already has an image.

## 2. Workflow

```
sync Anki ──► flag cards in Anki ──► plan ──► inspect report.html ──► apply ──► sync Anki
 (pull phone-set flags)                        (--reject/--approve)     (│ revert if unhappy)
```

Planned commands (`python -m decks.language <command>`):

| Command | Effect |
| --- | --- |
| `status` | Read-only: counts per deck/flag/tag, AnkiWeb size headroom, lint (misfiled notes, wrong-voice audio, missing media) |
| `fetch-data` | Explicit, idempotent download of open datasets (CC-CEDICT, Tatoeba) into `data/` |
| `plan --deck <d> --need audio\|context\|image` | Reads flagged cards, writes `runs/<id>/plan.json` + `report.html`. **Writes nothing to Anki.** |
| `apply --run <id> [--reject nids] [--approve nids]` | Applies the plan card by card (see §3) |
| `revert --run <id>` | Restores fields/tags/flags from the run's journal |
| `review` (images only) | Local page with the contact sheet; writes your picks |

Before `apply`, close the Browser window or select a different note: Anki's open editor keeps its own copy of a note and can overwrite the script's edit. `apply` also skips any note selected in the Browser or being reviewed, but don't rely on that alone.

## 3. Safety model

Anki stays **open**; the scripts talk to it through the **AnkiConnect** add-on (already installed). Every write goes through the same Rust code path the GUI uses, so `mod`, `usn`, `csum`, `sfld` and tags are handled correctly and the change syncs incrementally.

Per card, in this order:

1. Re-read the note. Require its raw fields to **equal** the plan's `before` and the flag to be unchanged. Otherwise skip and report (the user edited it since `plan`).
2. Append `before` + intended `after` to `runs/<id>/journal.jsonl` **before** writing.
3. Store media; `updateNoteFields`; read the note back and compare (NFC-normalised) to the intended text.
4. Remove the `needs-review` tag if present.
5. **Clear the flag last**, as its own call: `(raw & ~7) | n`, result checked.
6. Journal `committed`.

A crash before step 5 leaves the flag set; the next run classifies the card `complete-clear-flag-only` (needs approval, never cleared silently). On failure the card **keeps its flag and gains the tag `needs-review`**; queries exclude that tag unless `--include-needs-review`.

**Restore story.** AnkiConnect writes bypass Anki's undo. The journal is the per-run undo: `revert` restores a note only if its current fields still equal the journaled `after` (otherwise it reports "edited since apply") and reverts tags as a delta. Added media files stay (harmless). As a coarse safety net, `apply` requires a fresh Anki auto-backup (newest `User 1/backups/*.colpkg` younger than N hours) or `--i-have-a-backup`. A `.colpkg` restore replaces the *whole* collection, Jeopardy work included.

**Why not AnkiConnect `exportPackage` as the backup:** it blanks flags on export, an import only overwrites notes older than the export, it skips deck id 1 (= Français), and it runs synchronously on the GUI thread.

**AnkiWeb size ceiling.** AnkiWeb rejects collections over 314,572,800 bytes; the collection was 299,900,928 bytes on 2026-09-25 (≈14.7 MB headroom). `status` prints the headroom and `apply` refuses when the summed field growth exceeds a threshold. (Media is not counted toward this limit.)

**Sync.** Sync **before** `plan` so phone-set flags are present, and after `apply`. There is no `--sync-after`: AnkiConnect's `sync` raises after a Jeopardy full-upload. A Jeopardy refresh forces a full upload that would discard un-synced phone-side language edits.

**Stays out of scope:** the *NFL Fantasy Playoffs 2026* deck (decision 2026-09-25) and any deck not named in `language_consts.py`.

## 4. Backend decision (Anki access)

| Option | Verdict |
| --- | --- |
| **A. AnkiConnect, Anki open** | **Primary.** Flag → run → results appear; GUI write path. Needs a smoke test on Anki 26.9.2 first (not yet run there). |
| C. Headless `anki` pylib, Anki closed | **Documented fallback, not built.** Wheel `anki==26.9.2` exists (cp310-abi3, manylinux_2_35); pin to the GUI's version — a newer library risks `FileTooNew`. Cannot open a collection the GUI holds. |
| B. Raw SQLite, Anki closed | **Rejected for note edits.** The repo's Jeopardy scripts do this, but `field_checksum` matches Anki's `csum` on only 2,130 of 5,409 language notes (HTML-rich fields), some writers skip `mod`/`usn`, and it bypasses tag registration. Read-only `mode=ro&immutable=1` is fine for diagnostics **only while Anki is closed**. |

AnkiConnect facts that shape the code (from the installed source; details in `docs/ANKI_NOTES.md`): no dedicated flag action (use `setSpecificValueOfCard`, `keys=["flags"]`, one card per call); `multi` does **not** abort on an error; `updateNoteFields` silently ignores unknown field names; `cardsInfo.mod` is the *card's* mod, `notesInfo.mod` the note's; all calls run on Anki's main thread and freeze the GUI while they run.

## 5. Card schema (measured 2026-09-25 on the live collection)

All three decks use the stock **Basic** notetype (id 1556219583788: fields `Front`, `Back`; one card per note; no filtered decks). Template: question `{{Front}}`; answer `{{FrontSide}}<hr id=answer>{{Back}}`. CSS is `.card{font-family:arial;font-size:20px;text-align:center;…}` with **no image sizing**. There is no separate definition or sentence field, so all automation edits HTML inside two strings.

| Deck | Anki id | Cards |
| --- | --- | --- |
| Español | 1703865136849 | 157 |
| Français | 1 (Anki's renamed *Default* deck) | 1,799 |
| 中文 | 1575506557597 | 3,453 |

Everything on the **Front** shows on the question and repeats above the line on the answer; **Back** shows only on the answer. Convention: Front = English gloss/sentence + image + definition; Back = target-language headword + `[sound]` + (Chinese) pinyin + optional target-language sentence.

Token vocabulary used by the parser: `S` `[sound:…]`, `T` text, `I` `<img>`, `D` `{definition}` block, `P` pinyin, `C` a pure-CJK line. Sound position is **not** consistent, so the parser tokenises lines rather than relying on positional regexes.

### Français (1,799)

- Back: `TS` 83.6 %, `TST` 10.1 %, `TSTS` 1.5 %, `TPS` 1.0 %, `T` (no sound) 0.7 %. Sound at end 85 % (space before the tag ≈86 %), middle 14 %.
- Front: `T` 35.7 %, `I` 18.8 %, `TI` 13.0 %, `IT` 11.3 %; 53.4 % have an image, 1.1 % two or more.
- 16.5 % of Fronts carry a plain-text French definition (Larousse style, sometimes numbered "1. …" or with larousse.fr links); only 7.7 % use `{…}`.
- Word/phrase length of Back: 749 single-word, 604 two-to-three words, 446 four-plus (≈25 % are already sentence pairs).
- 13 cards have no sound; 33 have more than one.

### Español (157)

- Back: `TS` 95.5 %, sound at end 96 %, every card has a sound. Front: `T` 54 %, 29.9 % have an image.
- ≈4 cards carry an in-Spanish DLE-style definition ("tr. Conseguir lo que se intenta"); 2 have a separate sentence sound.
- 59 single-word, 48 two-to-three-word, 50 four-plus.

### 中文 (3,453)

- Orientation: English-front 62 %; image/definition-only front 20.5 %; **Chinese on the Front** 17.6 % (606 cards with CJK in Front and none in Back; 574 by the stricter pure-CJK-line rule; 633 with any CJK on the Front). For those cards the Back starts with pinyin and the sound follows it.
- Back shapes are highly varied (281 distinct): `CPS` 24.8 %, `CSP` 16.1 %, `CPSC` 8.5 %, `PSC` 7.1 %. Sound position: middle 55 %, end 37 %, start 3 %; 502 cards have the sound alone in its own `<div>`.
- Front definitions: 58.5 % have two or more `{…}` blocks; 29.6 % use the `[whole-word: {char1 defn}{char2 defn}]` wrapper; `{}` blocks equal the character count in 80 % of cases. These glosses are CC-CEDICT-style.
- Messy HTML to tolerate: empty `<b></b>` artefacts (25.6 %), 25 unbalanced-brace notes, unbalanced `<div>` (51), `<pre>` (14), `<li>` in French (5), `&nbsp;` (1,136 zh / 526 fr / 33 es), 200 notes with raw `\n` inside fields (soft wraps, not line breaks), 37 Purple Culture links.
- Only 121 cards (3.5 %) have the full triple: English sentence on the Front, Chinese sentence on the Back, pinyin for that sentence.

### What a *finished* card looks like

- **Chinese, orange-complete** (the 部署 card): Front = English gloss, English sentence, image, `[whole: {char}{char}]`; Back = headword, `(Pinyin)`, `[sound]`, Chinese sentence, `(Sentence pinyin)`.
- **French, orange-complete** (e.g. Pétrir): Front = English sentence, image, plain French definition; Back = headword `[sound]`, French sentence.
- **Spanish** is the same as French with an in-Spanish definition.

### Media

`collection.media` has 8,955 files: 4,674 `google-*` (AwesomeTTS), 2,843 `paste-*`, 370 `images-*`, 98 `googletts-*`. No sound/image reference in the three decks points at a missing file (one hotlinked `encrypted-tbn0…` image in one Chinese note).

- `paste-<sha1>.jpg|png`: 2,848 refs, median 8 KB, ≈275×184.
- `images-<sha1>.jpg`: 367 refs, median 18 KB, ≈395×293 — dragged Google thumbnails; the `alt` attribute holds the source page title (which **leaks the answer** if the image fails to load — new images use a neutral `alt=""`).
- Bare `<epoch><digits>.jpg` names (277 refs, apparently from a phone) and assorted others.
- 3,484 of 5,409 cards (64 %) already have an image; the ≈1,925 without skew toward sentences, negations and function words.

## 6. Blue: audio and pinyin

### What AwesomeTTS actually did (reproduced exactly)

- **Service/voices.** AwesomeTTS is configured with three Google Translate presets: `zh-CN`, `fr`, `es`, speed 1.0, hashed filenames (`config.db`). Voices in use: zh-CN 1,793 Chinese notes, zh-TW 467 (mostly 2021-22), zh-Hans 15.
- **Request.** `GET https://translate.google.com/translate_tts` with `ie=UTF-8`, `q=<chunk>`, `tl=<voice>`, `total=<n>`, `idx=<i>`, `ttsspeed=1.0`, `textlen=<len>`, `client=tw-ob`. Chunks ≤100 characters (split at `.?!。`, then `,;:、`, then space, then `-`); chunk MP3s are concatenated byte-for-byte. Accepted only if `Content-Type: audio/mpeg` and each chunk ≥1024 bytes. Output today: MPEG-2 Layer III, 24 kHz, mono, 64 kb/s.
- **Filename.** `google-<h[:8]>-<h[8:16]>-<h[16:24]>-<h[24:32]>-<h[32:]>.mp3` where `h = sha1(f"{text}/google/speed=1.0;voice={code}")` (options sorted by key, `k=v` joined by `;`). Example: 悬崖 with `zh-CN` → `google-8ca9f33a-3e27a639-df013ab6-f3e5ffb2-83ac0a61.mp3`. Reproduces for ≈1,180 of 1,439 single-sound French cards, 144 of 157 Spanish and ≈1,500 zh-CN Chinese notes (misses are mostly foreign-named files or text edited after generation).
- **The name is derived from the text, not the bytes**, and Google's audio drifts over time (today's clip for a word differs byte-wise from last year's). So: cache the MP3 keyed by that name, check the media folder first, and always use the name `storeMediaFile` *returns* (Anki renames on collision — the origin of 44 `…-<sha1>.mp3` files).
- **Text spoken.** AwesomeTTS' parenthesis/bracket/brace stripping is **off**, yet the data shows Chinese pinyin was never spoken: for recent Chinese cards 353 of 379 hashes match **line 1 (the CJK headword) only**; for French/Spanish 1,180/1,439 and 144/157 match the whole single-line Back. Example sentences are silent (only ≈20 French and 2 Spanish cards carry a second, sentence-level sound). So the workflow was: audio while Back held only the headword; pinyin added afterwards.
- **Insertion.** All audio came from AwesomeTTS' per-note editor dialog, which appends ` [sound:x]` (with a space) to the field. The mass generator has never completed a run here.
- **No API.** AwesomeTTS is GUI-bound (`aqt` at import) — it cannot be called from a script. The script therefore re-implements the ≈15-line HTTP call.

### Rules the script follows

- Speak the **headword only**: Chinese = the pure-CJK headword line, wherever it is (Front for ZH-front cards); French/Spanish = first text line of Back, with HTML, `[sound:]` and pinyin stripped. `zh-CN` for Chinese (no automatic zh-TW).
- Classify existing sound tags: `google-hash` / `legacy-googletts` / `foreign-name` (e.g. `规则.mp3`, `reporter.mp3` — 467 zh, 165 fr, 2 es). **Any existing `[sound:]` ⇒ `skip(has-audio)`** unless `--replace-audio`; 2+ sounds ⇒ `skip(multiple-audio)`. A hash mismatch is never treated as "stale".
- Placement: append ` [sound:x]` at the end of the headword line; Chinese headword-on-Back: headword, `(pinyin)`, then the sound; ZH-front cards: pinyin and sound go in the Back (their existing shape).
- Engine behind an `AudioEngine` Protocol. Google now; Azure Speech (free tier 0.5 M neural chars/month, official, ≈67 K chars needed for the whole deck) is a possible later swap with filename prefix `azure-` so old and new audio never collide.
- **Risk, on the record:** Google's `robots.txt` on `translate.google.com` has `Disallow: /translate` (covers `translate_tts`) and Google's ToS bars automated access that violates it; there is no SLA and the endpoint can be blocked any time. It is the same request AwesomeTTS makes, now scripted. Mitigation: 1-2 s between requests, back-off on 429/503, on-disk cache, bounded retries.
- Known, not touched unless flagged blue: 462 Chinese clips generated with the zh-TW voice; two Chinese notes with French-voice audio (荒漠, 芹菜); 44 collision-renamed files.

### Pinyin (Chinese)

Convention measured over 3,456 existing groups:

- `(Pinyin)` in parentheses on the Back, own line or after the sound; precomposed NFC tone marks (no combining marks, no tone numbers), `ü` precomposed, ASCII punctuation, ASCII `'` for apostrophes.
- **Word-level spacing**, first letter capitalised (84.8 %; 15.2 % lowercase). 2019 notes use per-syllable lowercase spacing; ≈154 legacy cards use `[ zhuānyè ]`; 107 cards carry `(繁體)` traditional forms in parentheses.
- 2-char headwords are one word 84 % of the time; 4-char idioms split 2+2 in ≈70 % (`Zhèngdà guāngmíng`). 的 is standalone in 116 of 162 cards, 了 is always attached, 不 is standalone in 14 of 39, 们 always attached.
- Generation: CC-CEDICT longest-match segmentation capped at 3 characters (so 4-char idioms split 2+2), 了 attached, 的/不 separate, tone numbers → NFC marks, first letter capitalised; `pypinyin` only for characters CEDICT lacks. Emit the dominant format `(Capitalised word-spaced)`.
- **Reading existing pinyin** must not rely on delimiters or a tone-mark test (neutral tones have no mark; `[…]` also wraps CEDICT glosses; `&nbsp;` and full-width parens occur). Classify by valid-syllable inventory + agreement with the CJK character count.
- Backtest metric: tone-marked **syllable-sequence** match ignoring case, spacing and brackets, target ≥95 %. Report format-exact match separately — it will be lower (16 % of 2-char headwords have split syllables, 19 % start lowercase, 210 cards use brackets).

## 7. Orange: context — sources and verdicts

Goal: a **definition** and a **dictionary example sentence**; the English translation of the example goes on the Front, the target-language sentence on the Back (per the finished shapes in §5). Cards whose Back is already a full sentence (≈25 % of French, ≈8 % of Chinese) skip context and are reported.

Offline coverage measured against the deck's headwords (exact surface form; lower bounds):

| Language | Source | Coverage |
| --- | --- | --- |
| 中文 (3,299 headwords) | CC-CEDICT whole-word | 80 % (97 % of 2-char, 68 % of 3-char, 34 % of 4-char) |
| | Tatoeba cmn-eng pairs (≤30 chars) | ≥1 pair 60 %, ≥3 pairs 44 % |
| Français (1,799) | Tatoeba fra-eng, single-word cards | 79 % exact, ≈90 % with crude stemming |
| | Tatoeba, 2-3-word / 4+-word cards | 42 % / 10 % |
| | Wiktionnaire French gloss | 783 of 1,787 normalised headwords |
| Español (157) | Tatoeba spa-eng, single-word cards | 50 of 59 (2 of 50 for 4+ words) |
| | es/en Wiktionary | 65 / 73 entries |

Source verdicts:

| Source | Verdict |
| --- | --- |
| **Larousse** (`larousse.fr/dictionnaires/francais/<mot>`) | **Used, by the user's decision (2026-09-25), with the risk recorded.** Definitions in `DivisionDefinition`, examples in `ExempleDefinition` (short French fragments, no English). No CAPTCHA seen at low volume, robots.txt allows the paths, no official API. **Their CGU (`larousse.fr/infos/cgu`) grants "besoins strictement personnels et privés" only and opposes automated harvesting/text-and-data mining (CPI L.122-5-3).** Mitigations: ≥2 s between requests, cache every page, stop on 403/429/CAPTCHA, honour robots.txt, personal use only, isolated behind a provider interface so it can be switched off. |
| Wiktionnaire (via kaikki per-word pages / MediaWiki API) | French definition fallback. CC BY-SA 4.0 / GFDL. Examples are long literary quotes with no English. Fetch on demand with a disk cache (no 718 MB bulk dump). The fr REST `/page/definition` endpoint returns 501. |
| **Tatoeba** | Example pairs with human English translations. Per-language exports (`downloads.tatoeba.org/exports/per_language/{fra,spa,cmn}/…`, ≈25 MB total), CC BY 2.0 FR — keep the sentence id as provenance. Mandarin has `cmn_transcriptions` (pinyin as tone numbers). |
| **CC-CEDICT** | Chinese definitions and readings. CC BY-SA 4.0, 125,109 entries, 3.98 MB gz at `mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz`. Use the dump, **not** the MDBG site ("automated or scripted access is prohibited"). |
| en/es Wiktionary | Spanish definitions and some translated examples; on demand, cached. |
| Le Robert, CNRTL/TLFi | Rejected (TDM opt-out / 403 / JS app). |
| RAE DLE, SpanishDict, Reverso Context, Linguee | Rejected: Cloudflare 403 to scripts and/or terms forbid automated use. Manual lookup only. |
| Purple Culture | Rejected: Cloudflare 403 on sample sentences; robots disallows paging. |
| Pleco | Rejected: no API (URL scheme only opens the app; exports only user dictionaries). |
| Youdao / iCIBA / Baidu / Bing examples | Undocumented, ToS unverified — last resort only. |
| **Claude (headless)** | Fallback for anything uncovered. Same `claude -p --output-format json` pattern as `classify_categories.py`; no API key. |

Fallback chains (first hit wins; every result carries provenance in `plan.json`):

- **French** — definition: Larousse → Wiktionnaire → Claude. Example: Larousse example (French → Back) with the English translation on the Front (Claude, marked as machine translation) → Tatoeba fra-eng pair → Claude.
- **Chinese** — keep existing `{…}` glosses; add the CEDICT entry, disambiguated by the card's pinyin. Example: Tatoeba cmn-eng (≤20 chars, shortest first) → en.Wiktionary → Claude. Fill only the missing parts of the finished shape.
- **Spanish** — definition in Spanish (es.Wiktionary → Claude), example: Tatoeba spa-eng → Wiktionary → Claude.

**Claude-drafted content** is validated before you see it (headword present in the sentence, `pypinyin` agrees with Claude's pinyin, ≤20 chars / ≤15 words, definition doesn't contain the headword, English has no CJK), is highlighted in the report, and defaults to **not approved** (`apply --approve <nid,…>`).

Headword matching: strip `[sound:]`, HTML, leading articles/pronouns (le/la/les/un/une/l'/se/s'; el/la/los/las/un/una/lo/se). Chinese: pick the pure-CJK line from Front or Back (441 cards have the pinyin first and the Chinese on a later line or in a `<div>`).

## 8. Green: images

The pipeline is **human-in-the-loop; nothing is applied unreviewed.** Honest expectation: ≈40-50 % of flagged cards one-click accepts (concrete nouns), 20-30 % need browsing, 25-35 % are unimageable (abstract words, grammar, negation, long sentences). The remaining no-image backlog is harder than what was done by hand. **Measure the accept rate on a 100-card dry run before committing to more.**

Candidate sources (checked 2026-09-25):

| Source | Free tier / key | Notes | Verdict |
| --- | --- | --- | --- |
| **Openverse** | No key; anonymous **burst 20/min, sustained 200/day** (register OAuth for more) | CC-licensed (filter `license_type`); good for concrete nouns, weak for abstract | **v1** |
| **Wikimedia Commons + Wikidata P18** | No key; 200 req/min with a descriptive User-Agent (10/min if anonymous), ≤3 concurrent | Standard thumbnail sizes only; Wikidata `wbsearchentities` in `fr/es/zh` gives disambiguated concrete nouns | **v1** |
| Serper.dev (Google Images JSON) | 2,500 free queries, key needed | Closest to your current Google workflow; unlicensed images; grey ToS; personal use only | Optional, off unless `SERPER_API_KEY` set |
| Pixabay / Pexels | Free key (100/60 s; 200/h, 20 K/month) | Stock style; Pixabay needs download-not-hotlink + 24 h cache; Pexels needs attribution | Optional |
| Unsplash | 50/h demo | Hotlinking required; non-English search beta only | Skip |
| SerpApi | 250/month free | Same legal exposure as Serper | Only if Serper unavailable |
| Brave Image API | $5/month credit (free tier removed) | ToS bars storing/caching results | Skip |
| DuckDuckGo scraping (`ddgs`) | none | "educational purposes only", fragile, rate-limited | Avoid |
| Direct Google Images scraping | — | Needs JS rendering; violates ToS | No |
| **Google Programmable Search JSON API** | — | **Closed to new customers; existing customers until 2027-01-01** | Dead |
| **Bing Search APIs** | — | **Retired 2025-08-11** | Dead |
| **Imagen 4** (AI generation) | — | **Shut down 2026-08-17** | Dead |

AI generation is only an on-demand fallback for imageable *sentences* (`gemini-3.1-flash-image` ≈$0.067/image, `gpt-image-2`); it can't depict negation/tense, may render answer-leaking text, and is deferred.

Design:

- **Query**: target-language headword + English gloss variants; a Wikidata gate for concrete nouns; sentences get a 3-6 word imageable query (Claude) or are marked unimageable and skipped with a reason.
- **Candidates**: up to 8 per card, ≥250 px, later perceptual-hash dedupe.
- **Review**: contact-sheet page served by a stdlib `http.server`; keys 1-8 pick up to 3, `0` skips. The only phase needing the interactive `review` UI.
- **Storage**: Pillow, long edge ≤400 px, JPEG q80, EXIF stripped, name `pic-<sha1[:12]>.jpg`; `<img src="…" alt="">` inserted after the gloss and before the definition blocks. JPEG rather than WebP for mobile-client parity. Source/licence/creator recorded in `plan.json`.
- **Later, only after real use**: Claude scoring of candidates (`depicts_meaning`, `has_text_or_watermark`, `ambiguous`, `offensive`). Unverified: whether headless `claude` can read candidate image files — the existing wrapper passes no tool flags; test `--allowedTools Read`/`--add-dir` first.
- Personal use assumed. Openverse NC/ND and Google-sourced images are not for redistribution — re-check before ever sharing the deck.

## 9. Code layout, conventions and dependencies

```
anki_common/            shared, stdlib only: consts, types (+ Protocols), helpers, anki_connect, llm
decks/language/
  __main__.py           CLI (logging.basicConfig only here)
  language_consts.py / language_types.py / language_helpers.py
  engine/               parse, render (offset splice), plan/apply/revert, journal
  audio/  context/  images/
  french/  spanish/  chinese/   per-language profile
  data/  runs/          gitignored: downloads, indexes, page caches; plans, journals, staged media
  tests/                unittest.TestCase, in-memory FakeBackend
```

Conventions (from `coding-requirements.md`): strict typing (`mypy --strict`, no `Any`, no `# type: ignore` — vendor `.pyi` into `stubs/`), constants in `*_consts.py`, types in `*_types.py`, pure logic in `*_helpers.py`, no function-scoped imports, `logging` not `print`, AnkiConnect replies parsed into `TypedDict`s by validating parsers, action names and query templates as constants. Tests only for pure logic. **The parser inserts by offset splice** (before the line's terminating `<br>`/`</div>`), never parse-then-reserialise, so untouched HTML stays byte-identical; its gate is `render(parse(x)) == x` for all 5,409 notes.

Dependencies (PDM only; declared in `[project.optional-dependencies]`): stdlib covers AnkiConnect, Google TTS, bz2/TSV parsing, CEDICT, the review server and the `claude` subprocess. New: `pillow`, `pypinyin` (vendored `.pyi`), and `pytest`. **Never `pdm add`/`pdm sync` against the shared `Clara/.venv`** that `.pdm-python` currently points at — create a project venv first (see Step 0 in the plan). Secrets (`SERPER_API_KEY`, Openverse OAuth id/secret) come from environment variables only and are redacted from logs and `plan.json`.

## 10. Decision log

| Date | Decision | Why |
| --- | --- | --- |
| 2026-09-25 | Repo grouped by folders (trivia / language), **no parent decks in Anki** | Anki hierarchy separators are `\x1f`, and several Jeopardy scripts look decks up by flat name |
| 2026-09-25 | NFL Fantasy Playoffs 2026 deck out of scope | Not trivia or language; must not be touched |
| 2026-09-25 | Anki access via **AnkiConnect, live** | Flag → run → results, GUI write path, correct `mod`/`usn`/`csum`/`sfld` |
| 2026-09-25 | **Preview, then apply**, with journal + revert | AnkiConnect writes bypass Anki's undo |
| 2026-09-25 | Claude drafts content when no source exists; review required | User choice |
| 2026-09-25 | Google Translate voices now, engine behind a Protocol | Voice/filename continuity with ≈4,600 existing clips; ToS risk documented |
| 2026-09-25 | Scrape Larousse for French definitions | User choice despite CGU; personal use, polite, isolated |
| 2026-09-25 | Tests for pure logic only | User choice |
| 2026-09-25 | No `exportPackage` restore point | Blanks flags, never overwrites newer notes, skips deck id 1, blocks the GUI |
| 2026-09-25 | Speak headword only; pinyin never spoken | Matches ≈93 % of recent Chinese audio hashes |
| 2026-09-25 | Any existing `[sound:]` ⇒ skip unless `--replace-audio` | Non-hash filenames exist on ≈10 % of cards; a hash mismatch does not mean "stale" |

## 11. Open questions

- Chinese audio: keep `zh-CN` only? The 462 legacy zh-TW clips and the two French-voice Chinese cards are left alone unless flagged blue.
- Should example sentences ever get their own audio? (Silent today.)
- Pinyin: normalise the ≈154 legacy `[ lowercase ]` cards and the 107 `(繁體)` annotations? Not planned.
- Failure policy on `needs-review` (keep flag + tag) — confirm after first real runs.
- Chinese definition language: English (CEDICT/en.Wiktionary) is the default; Chinese-language definitions cover only ≈46 % of headwords offline.
- Duplicate headwords (14 French groups, 47 Chinese groups): possibly intentional sense variants; not deduplicated.
- Leech-tagged (11 French, 70 Chinese) and suspended (5 Chinese) cards: not skipped — a flag is an explicit request.

## 12. Observations (not acted on)

- Chinese note 悬崖 (created 2026-09-24) sits in Français; `status` will list misfiles like it.
- `verify_refresh.py` (Jeopardy) reports CHANGED across any language edit.
- Anki's profile dir holds ≈2 GB of restore-point copies; root disk was 78 % full.

## 13. Roadmap

- [x] Discovery (read-only): collection, add-ons, AwesomeTTS internals, sources, images
- [x] Plan approved
- [x] Phase 0 — docs (this file, `docs/ANKI_NOTES.md`, root README, `decks/trivia/README.md`) — 2026-09-25
- [ ] Step 0 — AnkiConnect smoke test on a scratch profile with no sync key; dedicated project venv (needs the user)
- [x] Phase 1 — restructure into `decks/trivia/*` and `decks/language/*` — 2026-09-25 (working tree only, not committed; `--analysis-only` output byte-identical to before, mypy 27/ruff 3/125 tests unchanged)
- [ ] Phase 2 — `anki_common` client, parser/renderer (100 % byte-exact round-trip), plan/apply/revert core
- [ ] Slice A — blue audio for Français + Español
- [ ] Slice B — Chinese pinyin + audio (CC-CEDICT)
- [ ] Slice C — orange context (the 9 orange Chinese cards first)
- [ ] Slice D — green images (100-card dry run first)
