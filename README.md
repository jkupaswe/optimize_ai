# optimize_ai: Compressing Python for LLMs (a research post-mortem)

> **Status: archived research prototype (active Jul–Oct 2025). Not production-ready.**
> This README replaces the original project pitch with an honest, measured account of what the experiment set out to do, what worked, and why it did not reach its goal.
>
> ⚠️ Do not point the Claude Code proxy (`integrations/claude_wrapper/claude_proxy.py`) at files you care about. It overwrites the source file with translator output and does no syntax check or backup first.

---

## TL;DR

**The question.** Could I cut LLM token cost for coding work with a dense, machine-oriented notation for Python? The plan:
1. Compress Python into the notation ("CAI3-LITE", plus a macro layer called "FLOW").
2. Have the model (Claude Code) read and *edit* the notation.
3. Translate the model's reply back into Python deterministically.

**What worked.**
- The compressor is deterministic.
- It removes **40–68% of tokens** on medium and large Python files, before any instructions are added.
- An August 2025 local roundtrip (no LLM involved) regenerated Python that parsed for 5 of 5 test files and kept almost every function and class definition.

**What didn't:**

1. **The grammar tax.** The model needs ~**1,777 tokens** of format rules on every request to have any chance of emitting valid notation. That wipes out the savings for any file under roughly 3–4K tokens. Small files cost **7–8× more** than sending plain Python.
2. **Stochastic symbol drift.** The notation is new to the model, and the prompt asked it to produce identifier-mapping sections the compressor never sent it. So the model invented and renamed abbreviations differently from run to run. A 17-rule prompt, self-check lists and up to 3 repair calls could not make it adhere reliably.
3. **The encoding isn't invertible.** Distinct variables collapsed to the same short name, and library-method abbreviations were many-to-one. Decoding relied on a locally cached copy of the full AST, not on the compressed text. Even with no LLM in the loop, the roundtrip was **lossy**: code parsed but was semantically wrong. By the final commit it was **broken** (0 of 6 files produced parseable Python).

The economics and reliability problems are structural, not bugs. Section 6 covers what I would do instead.

**How to read the claims.** Statements are tagged by evidence:
- **[MEASURED]**: numbers from instrumented runs against this repo (method in §4).
- **[VERIFIED]**: confirmed by reading the code or git history.
- **[CLAIMED]**: from commit messages or earlier docs, not independently reproduced.
- **[INFERRED]**: interpretation.

Measurements were taken in Nov 2026 against `main` at `d3476c9` and the historical checkpoint `8dcc5cf` (`users/jkupaswe/mvp_v3_cai3`, Aug 16 2025).

---

## 1. Hypothesis

1. Much of what Python spends tokens on is "syntactic bloat": long identifiers, keywords, indentation, docstrings and boilerplate.
2. A compact, structured notation could carry the same program in far fewer tokens.
3. If the LLM reads *and writes* that notation, both input and output tokens fall.
4. A deterministic translator, using mapping tables kept locally, rebuilds correct Python. The user never sees the notation.

The idea depended on three things:
- **(a)** high compression,
- **(b)** an invertible encoding, and
- **(c)** an LLM that writes valid output in a notation it has never seen.

Only (a) was achieved, and only in part.

---

## 2. What I built [VERIFIED]

### 2.1 Format lineage
1. Python minifier.
2. SudoLang and SynthLang (prompt-style pseudo-languages).
3. **CAI v1**: lossy signature-plus-docstring summaries, e.g. `F[name(args) -> docstring]` (`examples/*.cai`).
4. **Waterfall compressor** that picks a strategy by file size: TINY passthrough, SMALL minify, MEDIUM full CAI3, LARGE chunked, HUGE summarized.
5. **CAI3**, which produces three artifacts, plus the **FLOW** macro language.

### 2.2 CAI3 data model (`input_optimizer/engine/cai3_structures.py`)
- **`CAI3Full`:** a complete AST-level record of the file, kept locally.
- **`CAI3Map`:** the local "side-car". It holds the name maps (`name_map` / `reverse_name_map`), span, byte, AST-path and parent/child maps, and the minified source.
- **`CAI3Lite`:** the compact text actually sent to the model.
- **Support types:** `CAI3Patch`, `CAI3Cache` (on-disk, keyed by content hash), and md5-based content IDs (`generate_cid`).

### 2.3 CAI3-LITE text format
- **Header:** `#C3L`.
- **Sections:**
  - `I:` imports
  - `D:` hash→value
  - `X:` body-id→cid
  - `P:` string pools
  - `S:` self-attributes
  - `V:` locals
  - `T:` symbol table
- **Bodies:** after a `---` separator, one `B#<id>:` body per function or method.
- **FLOW macros** (`flow_language.py`, ~5K lines):

  | Macro | Meaning |
  |---|---|
  | `R` | return |
  | `(cond){…}` | if |
  | `@v,iter{…}` | loop |
  | `T{…}X(e){…}` | try/except |
  | `!` / `&` / `\|` | not / and / or |
  | `N` | None |

Real compressor output for `examples/demo_project/main.py` [MEASURED]:

```
#C3L main.py
X:a0=23mx,a1=cwjs,a2=1c4t,a3=mjxy,a4=1liz
T:F:c#a0,F:pl#a1,C:c0,M:c0.i#a2,M:c0.m#a3,M:c0.d#a4
---
B#a0:Rx*y+10
B#a1:d=[];@d,d{(d>0)d.ap(ca(d,2))};Rd
B#a2:s.m=a
B#a3:Ra*b*s.m
B#a4:(b==0)RAW{RAW{raw_7662cde2}};Ra/b
```

It's compact, but look at `B#a1`. The original `process_list(items)` used three names (`items`, `item`, `results`), and **all three became `d`**. The text alone cannot be decoded back.

### 2.4 The Claude Code loop (`integrations/claude_wrapper/`)

1. **Compress.** `ClaudeCodeProxy.process_input` compresses the target file with CAI3.
2. **Wrap.** The result is wrapped in a constrained prompt (`claude_cai3_interface.py`). It has 17 "VIOLATION = FAILURE" rules, wrong/right examples, and pre-submission self-checks for brackets, hashes and variable mappings.
3. **Call the model.** The prompt is piped to the `claude` CLI.
4. **Validate and repair.** About 10 validators check the reply. Up to 3 repair prompts follow, each a full extra LLM call.
5. **Translate.** `process_output` merges the side-car `name_map` with the mappings the model returned. It then runs `CAI3Translator.translate_cai3_lite_to_python`: parse → resolve hashes, pools and variables → reconstruct Python.
6. **Write back.** The result is written over the original file.

### 2.5 Scaffolding (present, largely untested end-to-end)
- FastAPI service (`app.py`)
- CLI (`cli/`)
- VS Code extension and a Cursor stub
- Prometheus/Grafana configuration
- Docker/compose files
- A CAI v1 → Python translator (`output_translator/`)
- Generated docs

---

## 3. Timeline [VERIFIED from git history; milestone wording from commit messages = CLAIMED]

| Date (2025) | Branch(es) | Milestone |
|---|---|---|
| Jul 26–29 | early commits | Minifier, SudoLang/SynthLang translators, source mapping, output-to-Python mapping |
| Jul 30 | `cai_compression*` | CAI v1 and the tiered waterfall compressor |
| Jul 31 – Aug 2 | `output_translator_almost_functional*` | CAI → Python translator at ~60% of its test suite |
| Aug 3–9 | `e2e_functional_core_logic`, `mvp_v1`, `mvp_v1_claude_code` | MVP scaffolding plus the Claude Code proxy |
| Aug 10–12 | `mvp_v1_claude_code_*cai3*`, `mvp_v2_cai3*` | CAI3 introduced; reduction reported going from 1% to 14%, then "30–70%"; first roundtrip |
| Aug 16 | `mvp_v3_cai3` | Roundtrip without `pass` placeholders (**measured in §4.3**) |
| Aug 17 – Oct 6 | `mvp_v3_claude_code_wip` … `_pt16` | Getting Claude to edit CAI3 and translating its edits back (progression below) |
| Sep 9 | `mvp_v3_claude_code_wip_pt4_not_working` | Abandoned attempt; its debugging docs record 0% hash/pool resolution |
| Oct 6 | `_pt16` | Last development commit: "one shot translations, need to improve structure, fidelity and prompt token bloat reduction" |
| Dec 2025 | `main` | pt16 merged (PR #1); minor churn and reverts |
| Feb 2026 | `claude/research-project-architecture-UOywf` | AI-assisted retrospective (`PROJECT_PROSPECTUS.md`). Its metric tables are estimates, not measurements |

**pt-series self-reported progress** [CLAIMED]:
- pt5: "semi-working"
- pt8: "largely functional"
- pt10: "90% with method params"
- pt11: "95%, reconstruction kinks"
- pt12: "reducing prompt bloat → more accurate 1-shot"
- pt16: "one-shot translations"

These were spot-check impressions of individual Claude runs. No benchmark corpus or run logs back them, which is the main methodological lesson of the project (§6).

---

## 4. Measurements

### 4.1 Method
- **Tokenizer:** token counts use `tiktoken` `cl100k_base` as a proxy. Claude's tokenizer gives different absolute counts, but the ratios point the same way.
- **Test files:** six files from this repo, ranging from 233 to 5,097 tokens.
- **Identity roundtrip:** compress, then translate back with **no LLM edit**. This is the best case; if it fails, LLM-edited roundtrips can't succeed.
- **Code paths tested on `main`:** the translator was called directly, and the full `ClaudeCodeProxy.process_input` → `process_output` path was run with the compressed text fed back as if Claude had echoed it unchanged.

### 4.2 Compression vs. the grammar tax (`main`) [MEASURED]

| File | Original tokens | Tier | CAI3-LITE tokens | LITE reduction | Full prompt tokens | Net vs. plain Python |
|---|---|---|---|---|---|---|
| `examples/demo_project/main.py` | 233 | small | 140 | 39.9% | 1,918 | **−723%** |
| `tests/fixtures/sample.py` | 255 | medium | 374 | **−46.7%** (expanded) | 2,152 | **−744%** |
| `utils/cache.py` | 537 | medium | 302 | 43.8% | 2,080 | −287% |
| `examples/large_module.py` | 1,472 | large | 850 | 42.3% | 2,628 | −78.5% |
| `integrations/claude_wrapper/buggy_ecommerce_original.py` | 2,945 | large | 1,393 | 52.7% | 3,170 | −7.6% |
| `core/session_manager.py` | 5,097 | large | 1,891 | 62.9% | 3,668 | **+28.0%** |

- **Fixed overhead.** The constrained prompt adds a constant **~1,777 tokens** per request. Break-even is about `1,777 / reduction`, which works out to roughly **2.8K–4.4K original tokens**.
- **Guard disabled.** The proxy computes net savings itself. But the guard that would skip compression when it doesn't pay was commented out ("TODO: re-enable").
- **Determinism.** Compression gave identical output across runs for 6 of 6 files.
- **Not counted above:**
  - **Output tokens:** the model must re-emit the whole notation file plus new mapping sections.
  - **Repair loops:** up to 3 more full calls.

### 4.3 Roundtrip fidelity at the Aug 16 2025 checkpoint (no LLM) [MEASURED]

| File | LITE reduction | Parses | Definitions kept | AST-equal (ignoring docstrings) | Line similarity |
|---|---|---|---|---|---|
| demo `main.py` | 44.6% | ✅ | 6/6 | ❌ | 0.18 |
| `sample.py` | 29.4% | ✅ | 7/7 | ❌ | 0.92 |
| `utils/cache.py` | 52.1% | ✅ | 7/7 | ❌ | 0.91 |
| `large_module.py` | 51.7% | ✅ | 13/13 | ❌ | 0.87 |
| `session_manager.py` | 68.3% | ✅ | 32/35 | ❌ | 0.89 |

- **It depended on the cache.** With a fresh cache directory, translation failed for 5 of 5 files ("No CAI3 cache found … Ensure compression was run first"). This roundtrip rebuilt code from the locally stored full AST; it never decoded the LITE text.
- **It was lossy even with the cache.** Examples:

  | Original | Reconstructed | What went wrong |
  |---|---|---|
  | `datetime.now().isoformat()` | `file_path.now().is_directory()` | abbreviation `is` reversed to the wrong method |
  | `hashlib.md5(file_path.encode()).hexdigest()` | `h.md(file_path.en()).he()` | abbreviations leaked into the output |
  | `with open(...) as f: json.dump(...)` | *(removed)* | `with` block dropped |
  | `{"max_retries": 3, "timeout": 30}` | `{"#0949": 3, "#1504": 30}` | dict keys replaced by hash placeholders |
  | `logger.warning(f"…")` | `….wa(_container)` | `_container` placeholder leaked into the code |

- **The repo's roundtrip test passed 5 of 6** at this checkpoint. Its assertions only checked for a few substrings and `compile()`, so "passing" did not mean the code behaved the same.

### 4.4 Roundtrip on the final code (`main`, no LLM) [MEASURED]
- **Parse rate:** **0 of 6** outputs parse, whether the translator is called directly or through the full proxy path.
- **Line similarity** to the originals is 0.00–0.02.
- **The proxy overwrote all 6 input files** (test copies) with this broken output.
- Typical output:

```python
class C0Class:                         # was Calculator
    def unknown_m(self):               # was multiply
        Ra * b * s.m                   # FLOW 'R' (return) never expanded
    def unknown_d(self):
        (b==0)RAW{RAW{raw_7662cde2}}   # raw placeholder unresolved
    def process_list():
        d = []
        @d,d{(d>0)d.ap(ca(d,2))}       # loop macro left untranslated
```

- **Translator logs explain why.** They report "NO V: SECTION FOUND … cai3_map contains 17 mappings that Claude won't see", along with "No hash mappings / No pools / No variable mappings available".
- **A dead code path.** The translator also reads `cai3_map.variable_map`, which nothing in the codebase ever assigns.
- **[INFERRED]** During the pt-series the translator was reworked to decode *Claude-written* CAI3, which contains `S:`/`V:`/`D:` sections and Python-like keywords. It stopped handling the compressor's own FLOW output. The deterministic baseline regressed while the LLM path was being tuned.
- **Compression also got worse on `main`** than at the Aug checkpoint, for example `sample.py` went from +29.4% to −46.7%. The extra side-car scaffolding added to the LITE text costs tokens.

### 4.5 Test suite on `main` (each file run separately, 90 s timeout) [MEASURED]

| Area | Result |
|---|---|
| Minifier, SudoLang, SynthLang, AST mapper, source mapping, OTPM | ✅ ~120 tests pass |
| `test_cai3_roundtrip.py` | ❌ 5 failed: calls the renamed API `translate_lite_to_python` |
| `test_waterfall_compressor.py` | ❌ hangs |
| Semantic compression / adapter / API tests | ❌ failures, or undeclared dependencies (`httpx`) |
| Claude wrapper tests | ❌ 4 failed, 6 passed |
| `tests/` end-to-end and integration | ❌ import errors (missing modules, undeclared `rich`), or failures |

The early, simple components are healthy. The CAI3 roundtrip, waterfall, wrapper and end-to-end layers are stale or broken.

---

## 5. Why it failed

Root causes, most fundamental first:

1. **Per-request grammar cost.** Teaching the model a new language inside every prompt costs more than the language saves on typical edit-sized files. Output tokens weren't reduced either, because the model re-emits the whole file plus mappings.
2. **Prompt and compressor disagreed.**
   - The compressor emits FLOW macros and `RAW{…}` blobs, with no `S:`/`V:`/`D:` sections.
   - The prompt forbids raw data, requires *complete* `S:`/`V:`/`D:` sections, and shows examples with Python keywords.
   - So the model had to invent mappings for names it was never told about. That is the source of the symbol drift and non-adherence.
3. **Non-invertible encoding.**
   - Many-to-one abbreviations (`is` → `isoformat` / `is_directory`; `ex` → `exists` / `extend` / `expanduser`).
   - Collapsed local variables.
   - Dropped constructs.
   - Decoding depended on hidden local state, and merging LLM edits back into that state was never solved.
4. **Fighting the model's priors.** CAI3 doesn't exist in any training data. The ever-growing prompt (emoji alarms, 17 rules, self-checks, repair loops) was a symptom, not a fix. Constrained decoding and fine-tuning were never tried.
5. **No evaluation harness.**
   - Progress was judged by eye on single runs.
   - There was no fixed corpus, no semantic-equivalence check (e.g., re-running the original unit tests on regenerated code), and no net-token accounting. Without these, regressions went unnoticed (§4.4).
6. **Engineering debt.**
   - Multi-thousand-line files with large commented-out duplicates.
   - Stale tests and undeclared dependencies (no `requirements.txt`).
   - POSIX-only code (`fcntl`).
   - Source files overwritten destructively.

---

## 6. What I'd do differently

1. **Compress asymmetrically.**
   - Compress only *read-only* context: repo maps, signatures, and stripped bodies of files that won't be edited.
   - Let the model read and write ordinary Python.
   - Use compact *edit formats* (search/replace blocks or unified diffs) to cut output tokens.
2. **Amortize any grammar.** Put a custom notation's spec in a cached system prompt (prompt caching) instead of paying for it on every request.
3. **Constrain, don't plead.** Use grammar-constrained decoding (a CFG or schema) instead of rule lists and repair loops. Alternatively, fine-tune a small model on Python ↔ notation pairs.
4. **Make the encoding bijective and self-contained.**
   - Scope-aware unique short names.
   - Leave external and library names uncompressed.
   - No opaque placeholders in model-visible text.
   - Any mapping the model needs goes in the prompt.
5. **Measure first.**
   - Use the target model's tokenizer.
   - Build a fixed corpus and property-based roundtrip tests (AST equality plus the original test suites).
   - Run batched real-LLM evaluations that report adherence rate, repairs per success, semantic correctness of edits, and *net* tokens (input + output + retries) against a plain-Python baseline.

---

## 7. Repository map

| Path | What it is |
|---|---|
| `input_optimizer/engine/` | Compressors: minifier, waterfall, CAI3 (`cai3_*.py`), FLOW (`flow_*.py`) |
| `input_optimizer/engine/cai3_ast_translator.py` | CAI3-LITE → Python translator |
| `integrations/claude_wrapper/` | Claude Code proxy, constrained prompt, validators and repair loop |
| `output_translator/` | CAI v1 → Python translator |
| `cli/`, `app.py` | CLI and FastAPI service |
| `integrations/vscode/`, `integrations/cursor/`, `monitoring/`, `Dockerfile`, `docker-compose.yml` | Editor, observability and deployment scaffolding |
| `examples/`, `tests/`, `input_optimizer/tests/` | Samples and tests (see §4.5 for their state) |

### Reproduction notes
- **Environment:**
  - Use Python 3.11+ on Linux/macOS; on Windows, stub out `fcntl`.
  - Put the repo directory, named `optimize_ai`, and its parent on `PYTHONPATH`.
  - Packages used: `tiktoken`, `astor`, `black`, `click`, `pyyaml`, `pydantic`, `fastapi`, `pytest`, `pytest-asyncio` and `pytest-timeout`.
  - `utils/metrics.py` imports `sentence-transformers` at module level.
- **Compress:** `CAI3WaterfallEnhancement()._standard_compression_cai3(content=..., original_tokens=..., source_path=...)`, after setting `include_all_methods = True`.
- **Translate back:** `CAI3Translator(cache_dir).translate_cai3_lite_to_python(lite, path, claude_mappings, cai3_map)`.
- **Proxy:** `ClaudeCodeProxy(cache_dir=...).process_input(prompt, file_path)` → `process_output(reply, metadata)`. **Run it only on copies.**
- **Claude token counting:** `utils.metrics.count_tokens_claude` needs the `ANTHROPIC_API_KEY` environment variable.

---

## License

See [`LICENSE.txt`](LICENSE.txt).
