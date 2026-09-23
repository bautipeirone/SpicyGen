# LLM prompt history and current organization

## Scope and method

This report traces the prompt-related history reachable from the current `main` branch for:

- `src/prompts.py`
- `prompts/` (including renamed and deleted predecessors)
- `src/agent.py`

The history was reconstructed with path-specific `git log`, `git log --follow`, and commit diffs. Dates below are author dates. Commit messages are quoted or closely paraphrased; explanations of the performance problem a change appears to address are explicitly described as interpretations when the diff does not state the motive.

The repository has a nested Git root at `code/grammar-generator`; therefore the paths in this report are relative to that directory.

## Executive summary

The prompt architecture evolved in three broad phases:

1. **Compiler-only correction loop (March-April 2026).** The model generated Spicy, received compiler errors, and retried. Tool-use nudges and termination handling were added because models sometimes returned code without calling the compiler or stopped the AutoGen conversation incorrectly.
2. **Runtime-guided and modular prompting (April-June 2026).** Packet execution, parse-tree feedback, best-candidate retention, RFC summarization, target-language abstraction, and dynamically assembled system prompts were added. The language manuals moved into per-language/per-protocol-type Markdown files.
3. **Explicit failure-prevention contracts (June-August 2026).** The prompts became increasingly prescriptive about parser state, constraints, external parameters, negative packets, exact entrypoints, length semantics, branch ambiguity, synchronization, byte order, unsupported syntax, and output-only requirements. Two tools (`compile_grammar`, `parse_packets`) were combined into `validate_grammar` to reduce sequencing failures.

The strongest general pattern is that short amendments were added after concrete model mistakes, while the newer 3D and Vest manuals were written as stricter “closed world” language references: only documented syntax is allowed, the entrypoint is fixed, invalid constructs are called out, and final checklists restate the most important invariants.

## Current prompt layout

### Grammar-generation path

The current grammar-generation system message is assembled in this order by `build_refinement_system_message()`:

1. `BASE_GENERATION_PROMPT`: role, objective, completeness, tool use, and the instruction to consult additional RFCs when necessary.
2. One language manual loaded from `prompts/` according to target language and protocol type (unless the caller supplies a custom instructions file).
3. An optional `EXTERNAL PARSER ARGUMENT CONTRACT`, generated from protocol metadata.
4. `AGENT_LOOP_INSTRUCTIONS_TEMPLATE`, specialized for parse-tree support and whether the RFC retrieval tool is enabled.

The user message is separate: the specification text (or `EMPTY_SPEC_PROMPT`) followed by “Please output the grammar and verify it with the given tools.”

At runtime, `src/agent.py` exposes `validate_grammar`, which first compiles and then runs positive/negative packet checks. Compiler or runtime output is returned to the LLM through dedicated feedback templates. An optional RFC retrieval tool is registered only when configured.

### Preprocessing prompts

`src/prompts.py` also contains three independent preprocessing prompts:

- `SUMMARIZE_SPEC_SYSTEM_PROMPT`: asks a tool-using agent to select exact non-overlapping RFC intervals relevant to grammar or state machines.
- `EXTRACT_INFO_SYSTEM_PROMPT`: asks for one exact JSON object describing protocol type, port, transports, layer, encoding, checksum, and external parser arguments.
- `NEGATIVE_PACKETS_SYSTEM_PROMPT_TEMPLATE`: asks for an exact count of close-to-valid, single-fault, raw protocol payloads in JSONL.

These do not use the grammar-language manuals.

### Prompt-manual directory

The current routing is:

| Target/use | Prompt file | Current organization |
|---|---|---|
| Spicy text protocols | `prompts/spicy/TEXT.md` | General rules; declarations; fields/bytes/regex/vectors; state, hooks, and validation; synchronization; line-oriented parsing; examples. |
| Spicy binary protocols | `prompts/spicy/BINARY.md` | General rules; declarations; fields/bytes/regex/vectors; state, hooks, and validation; synchronization; endian/bitfield considerations; examples. |
| EverParse 3D binary protocols | `prompts/3D/BINARY.md` | Strict output contract; file structure; scalar types; constants/enums/structs; provable constraints; bitfields; parameters; unions/switches; arrays; payloads; checksums; patterns. |
| Vest binary protocols | `prompts/vest/BINARY.md` | Strict output/root contract; declarations; file-wide endian; integers/constraints; arrays; dependencies; length expressions; enums/choices; bounded parsing; repetition/options/tail; limitations; patterns; final checklist. |
| Zeek integration | `prompts/zeek/INTEGRATION.md` | Three-file architecture; `.evt`; Zeek-side Spicy helpers; `main.zeek`; simple and complex examples; pitfalls. |

The Zeek guide is not part of grammar generation. `create_zeek_package.py` inserts it into a separate Zeek-specific system template, requires a three-key JSON bundle, supplies the parser source and exact module/analyzer metadata in the user prompt, and retries with parse or compiler errors plus the previous response.

## `src/prompts.py` history

### 2026-06-03 — `cbf81e0` — prompt composition extracted from `agent.py`

`src/prompts.py` was created to make prompts dynamically composable. It initially centralized:

- RFC interval summarization;
- protocol metadata extraction;
- the base grammar-generation role;
- the compile-then-parse workflow;
- conditional parse-tree and RFC-retrieval hints;
- construction of the full refinement system message.

The initial base prompt told the model to include all fields but **omit value constraints**. This policy was later removed.

### 2026-06-05 — `c5b0431` — empty-spec mode

Added `EMPTY_SPEC_PROMPT` and `build_empty_spec_prompt()`. When no RFC text exists, the user prompt now names the short and optional long protocol name and tells the model to form an initial hypothesis from general knowledge while treating it as untrusted.

This is a guard against presenting model memory as specification-grounded fact.

### 2026-06-25 — `0eb629e` — external parser argument contract

`build_refinement_system_message()` gained a `Protocol` argument and can inject an exact contract listing required external parser parameters. The model must:

- declare the public entry unit with exactly those names and types;
- not turn the values into parsed packet fields or constants;
- understand that runtime metadata supplies values by name.

This addresses protocols whose parse shape depends on negotiated, configured, session, link, or channel state absent from the packet bytes.

### 2026-06-25 — `6f36fc1` — extract external arguments during metadata preprocessing

The metadata JSON schema gained `parser_arguments` with stable `snake_case` names, neutral types (`bool`, `int`, `uint`, `string`, `bytes`, `float`), and short descriptions. The prompt sharply distinguishes genuine out-of-band context from normal fields, transport metadata, lengths, checksums, addresses, and ports.

The specificity is important: without the exclusions, an LLM could over-classify ordinary on-wire values as external arguments or emit target-specific types such as `uint32`/`size_t`.

### 2026-06-26 — `067607a` — negative-packet generation prompt

Added a JSONL-only prompt for an exact number of negative samples. Every sample must be close to valid, violate exactly one rule, and prefer useful boundary faults rather than random bytes. Suggested categories include bad/truncated lengths, illegal enums, missing terminators, inconsistent counts, bad checksums, and forbidden flags.

The metadata prompt wording also changed from “training data” to the less implementation-specific “general protocol knowledge.”

### 2026-06-29 — `4511335` — one validation tool

Replaced the four-step `compile_grammar` then `parse_packets` instructions with a shorter `validate_grammar` loop:

1. write and validate;
2. fix compilation or packet failures and validate again;
3. terminate on success;
4. terminate after compilation when no packets exist.

This mirrors the agent-side tool consolidation. The likely performance benefit is less tool-selection and sequencing burden: the model can no longer compile successfully and forget the runtime test.

### 2026-07-24 — `47b41eb` — negative packets are raw protocol payloads

Added one precise sentence forbidding IP, TCP, UDP, or other lower-layer encapsulation around generated negative packets. This is a classic small corrective amendment: it prevents otherwise plausible samples from being unusable by a protocol-level parser harness.

### 2026-08-14 — `54f5379` — stop telling the model to omit constraints

Changed the base instruction from the grammatically awkward “capture all the fields relationships” plus “value constraints ... must be omitted” to “capture all the fields and its relationships.”

The consequential part is deletion of the blanket ban on constraints. By this point the language manuals and negative tests explicitly encouraged enforcing constants, ranges, and structural validity. Keeping the earlier ban would have caused grammars to accept malformed packets and conflict with runtime validation.

## `prompts/` history

### Spicy manuals

#### 2026-04-30 — `a92322d` — initial split by protocol representation

Created `prompts/BINARY.md` and `prompts/TEXT.md` (230 and 180 lines). Both followed the same teaching layout: general rules, grammar syntax, declarations, fields, bytes, regexes, vectors, and examples. Text added line-based parsing; binary added protocol-specific considerations.

#### 2026-05-05 — `c1451b1` — line endings and unambiguous request/reply parsing

The text guide changed the example newline from optional CRLF/LF to strict CRLF and changed header-value termination to `\r\n`. It also added:

- a request/reply switch whose methods are explicitly enumerated;
- a correct optional-prefix pattern;
- a counterexample showing overlapping branches;
- an instruction to keep alternatives mutually exclusive.

The commit was associated with a stable IRC grammar. The diff strongly suggests it codified lessons from lookahead ambiguity and delimiter handling.

#### 2026-06-01 — `e76e69b` — directory routing

Renamed the files without content changes to `prompts/spicy/BINARY.md` and `prompts/spicy/TEXT.md`, making room for multiple target-language families.

#### 2026-06-02 — `34357b8` — prevent opaque whole-packet parsing

The text manual was tightened after Rlogin work:

- explicitly banned modeling the entire packet as `data: bytes &eod`;
- warned not to combine `&eod` with `&until`;
- required explicit methods in request/reply lookahead and called a generic `Token` wrong there.

This steers the model away from grammars that trivially accept packets but expose no structure or create ambiguous alternatives.

#### 2026-06-03 — `878121a` — exact boolean spelling

Added the small but compilation-critical rule that Spicy booleans are `True` and `False`.

#### 2026-06-29 — `c101deb` — parser state and semantic rejection

This was the largest Spicy quality amendment. Both manuals gained guidance and examples for:

- vector `&until` conditions;
- unit `var` state, optional state, and presence checks;
- unit parameters and `inout` parent updates;
- `on FIELD` and `on %init` hooks;
- deriving lengths, body presence, delivery modes, and other structural state before later parsing;
- `&requires` for local constraints and `throw` for invalid cross-field/derived conditions;
- using `if` only for legitimately absent optional fields, not to silently accept malformed input.

The text manual additionally gained a full HTTP-like parent/header/body/chunk pattern and broadened CRLF back to `/\r?\n/`. This superseded May's stricter CRLF example so generated parsers could accept common LF variants while still modeling line boundaries.

This change directly targets a common plateau: a grammar can compile and parse happy-path bytes while missing the state relationships needed to reject negative packets or parse later fields correctly.

#### 2026-07-27 — `a6331f6` — nonstandard integer widths and little endian

The binary manual added exact syntax for converting raw byte arrays to integers with `to_uint(ByteOrder)`, allowing widths such as three bytes, and gave the exact little-endian integer attribute.

#### 2026-07-29 — `938a831` — enum preference and length operators

The binary manual told the model to prefer enums for discrete opcodes/options and documented `|bytes|` and `|vector|`. These small additions avoid hand-written or invented length expressions and improve semantic output types.

#### 2026-07-31 — `db1f32c` — parity for text length operators

Added the same `|bytes|` and `|vector|` operators to the text manual.

#### 2026-08-12 — `f25d1dd` — explicit recovery from ambiguous switches

Added advice for the concrete compiler error `production switch is ambiguous for look-ahead symbol(s)`: factor the common prefix out of branches, or move validation into hooks rather than trying to distinguish branches through overlapping constraints.

#### 2026-08-12 — `2e580fb` — conservative error synchronization

Both manuals gained detailed `&synchronize`, `%synchronize-at`, `%synchronize-after`, and `confirm` guidance. The examples distinguish binary magic markers from text commands/delimiters. The prompt repeatedly warns not to invent recovery boundaries and not to add synchronization when a marker can occur in payloads or recovery would enter a dependent message midstream.

The binary bitfield example was also fixed to give the bitfield a semantic field name instead of making it anonymous.

### EverParse 3D manual

#### 2026-05-27 — `5edf390` — initial `prompts/3D_BINARY.md`

Added a compact 160-line language reference covering scalar types, structs, constraints, bitfields, constants/enums, parameters, tagged unions, arrays, and optional fields.

#### 2026-05-29 — `b939b8c` — unique entrypoint

Amended the struct rule to require exactly one `entrypoint` declaration for the protocol's main parser. This prevents the model from exposing multiple validators or producing an ambiguous root.

#### 2026-06-01 — `e76e69b` — strict rewrite and relocation

Replaced the compact guide with a 606-line manual at `prompts/3D/BINARY.md`. It introduced a strict output-only contract and organized syntax around file structure, endian-specific types, constants, enums, structs, F*-provable constraints, bit ordering/storage boundaries, parameters, tagged unions, inline switches, optionals, arrays, padding, checksums, naming, and common patterns.

The rewrite's repeated “do not invent” rules and proof-safe arithmetic examples are aimed at syntax hallucination and grammars that compile poorly because the generated F* obligations cannot be proved.

#### 2026-06-02 — `4eed1c0` — declaration order and byte-sized arrays

Added “declare and define types before using them” and removed the documented `T field[n]` element-count alternative. Variable arrays are now taught only as `T field[:byte-size n]` because 3D sizes these arrays in bytes. This removes a particularly damaging unit ambiguity.

#### 2026-06-02 — `203b31e` — multi-field switch alternatives

Told the model to wrap a switch branch in a `struct` when it needs multiple fields and added an exact inline example. This addresses models trying to place multiple naked declarations in a case.

#### 2026-07-29 — `938a831` — reinforce declaration-before-use

The top-level guideline was amended to say helpers must be ordered so every type reference points to an earlier declaration, reinforcing the June fix where models were still likely to generate forward references.

### Vest manual

#### 2026-08-03 authored / integrated 2026-08-14 — `11412ef` — initial WIP guide

Added a short 68-line syntax sketch for fixed integers, constraints, byte arrays, dependent fields, repetitions, options, enums, and choices.

#### 2026-08-15 — `c016515` — full runtime-oriented rewrite

Expanded the WIP prompt to 589 lines and made it a strict language contract. Major additions include:

- output only one `.vest` file;
- exactly one unparameterized root struct named `message`;
- declaration punctuation and identifier restrictions;
- file-wide byte order and the lack of per-field override;
- exact integer constants, constraints, arrays, dependency marking, and type matching;
- static-size and guarded length arithmetic;
- closed/open enums and exhaustive/non-overlapping choices;
- byte-bounded subformats with `>>=` versus element-count arrays;
- safe repetition, option, and `Tail` rules;
- a list of unsupported features to prevent invented Rust/Vest syntax;
- common wire-format patterns and a ten-point final checklist.

This is the clearest example of prompt hardening against LLM improvisation: the prompt defines both valid constructs and tempting invalid alternatives.

### Zeek integration manual

#### 2026-06-15 — `49bf242` — initial integration guide

Added a 362-line guide for generating the three integration files (`.evt`, Zeek helper `.spicy`, and `main.zeek`), including the event flow, module/name consistency, scalar and complex bridging, logging, worked TFTP/DHCP examples, and common pitfalls. It was introduced together with a refinement/validation loop for integrations.

#### 2026-06-19 — `29eb90b` — DPD-compatible analyzer declarations

Changed singular analyzer naming to one analyzer per transport (`<Analyzer>_<Transport>`) and removed hardcoded `port ...` clauses from examples. The package workflow had moved to Dynamic Protocol Detection signatures, so port bindings in generated analyzers were both redundant and potentially wrong for nonstandard ports.

## `src/agent.py` prompt-affecting history

`agent.py` contains fewer static prompts today, but its tool descriptions, feedback messages, automatic nudges, and termination rules materially determine what the LLM sees next. The timeline below excludes logging-only and metrics-only commits.

### 2026-03-24 — `e9f1a5d` — compiler-error refinement begins

The first implementation was a direct retry loop. It sent compiler diagnostics through `FEEDBACK_TEMPLATE` and demanded only corrected grammar. This established the core pattern: execution feedback becomes the next user message.

### 2026-03-30 — `c2502e6` — AutoGen tool loop

Refactored generation into assistant/proxy agents and registered `compile_grammar`. The system message told the model to call the tool and say `TERMINATE` after success; the initial user message repeated the tool-use request. This duplication was intended to make tool calling more reliable.

### 2026-03-30 — `06a1e01` — RFC retrieval

Registered `retrieve_rfc_by_id` and told the model in the system message that it could fetch dependencies. This allowed grammars to follow referenced RFC structures rather than guess them.

### 2026-03-31 — `933eacb`, `120aa6e` — tool-call nudging, especially for Gemini

Added `default_auto_reply` so a plain model response would not end the loop; the proxy instead asked it to compile, repair, or terminate. The workflow instructions were moved into a clearly delimited constant in the commit titled “Gemini is now calling tools correctly.”

These are direct responses to an LLM orchestration failure rather than a grammar-syntax problem.

### 2026-04-08 — `bac72c8` — retry accounting and final-attempt instruction

AutoGen's reply budget was raised to three times the compilation-attempt budget because tool messages and nudges also counted as replies. At the true final compilation attempt, feedback explicitly said it was the last attempt and asked the model to terminate. This prevented tool chatter from exhausting retries early.

### 2026-04-09 — `1f013bf` — compile success is no longer enough

Added `parse_packets`, a runtime feedback template, and an ordered system workflow requiring packet checks after successful compilation. Runtime failures now named the failing packets and asked for a corrected grammar. The tool description explicitly said to use runtime testing after compilation.

This was the major shift from syntactic success to behavioral success.

### 2026-04-17 — `f02acac` — parse-tree context

Runtime checking could include parse-tree information, and success was moved from compilation to the all-packets-passed condition. The model could therefore use structural diffs instead of only a pass/fail label.

### 2026-04-23 to 2026-04-28 — `30503c7`, `4f9bc38`, `5c44675` — preserve progress and focus feedback

- Adjusted termination so the last permitted attempt was actually usable.
- Tracked and returned the grammar with the best packet score rather than blindly returning the final regression.
- Made packet representation and parse-tree output configurable, with packet representations included in runtime summaries only when requested.

These changes improve refinement outcomes even though they are not prose-only prompt edits: the model receives more useful evidence, and the pipeline keeps its best result.

### 2026-05-22 — `7e62061` — RFC interval-selection agent

Added the current summarization system prompt and a `select_intervals` tool. The model must copy exact start/end markers, avoid overlapping selections, include grammar/state-machine material, and exclude administrative, API, background, reference, IANA, and table-of-contents text. A default reply nudges it to call the tool.

This reduces context size while preserving exact source text instead of asking the LLM to paraphrase the RFC and risk losing constraints.

### 2026-05-26 — `31e5fcb` — target-aware runtime hints

After abstracting target languages, the system workflow only promised parse-tree summaries when the selected runtime supported them. This avoids instructing the model to rely on feedback it will never receive.

### 2026-06-01 to 2026-06-04 — `66b1c3c`, `08263e6`, `cbf81e0`, `ffd7c5f` — safer termination and modular prompts

- Required a real termination token instead of stopping whenever `TERMINATE` appeared anywhere in an ordinary response.
- Made RFC retrieval registration conditional on configuration.
- Moved reusable static prompts and workflow construction to `src/prompts.py`.
- Simplified the initial user message to specification plus a generic verification request because tool workflow was now in the assembled system message.
- Then accepted messages ending in `TERMINATE`, accommodating models that prefix a short explanation, and added a null-content guard to prevent termination checks from crashing on tool messages.

### 2026-06-26 — `8eba035` — default reply must request runtime tests too

The proxy nudge changed from “compile and fix” to “compile, then run packet tests, then terminate only if all pass.” The `parse_packets` description also changed from “after a successful compilation” to “after each successful compilation.” This closes a loophole where a model complied with the nudge but skipped semantic validation.

### 2026-06-29 — `4511335`, `29bc76c` — consolidate and repair tool guidance

Replaced the two public tools with one `validate_grammar` tool that compiles and, on success, tests packets. Updated runtime feedback, tool descriptions, default replies, and the system workflow to name the new tool. A follow-up removed an accidental one-element tuple in `default_auto_reply` that crashed AutoGen.

### 2026-08-06 — `9b8f801`, `68833e9` — exact retry boundary

Compile and parse helpers began returning success flags so `validate_grammar` could enforce the retry limit itself. The follow-up ensured that a successful final attempt is returned as success rather than being overwritten by “last allowed attempt; terminate.” This prevents the orchestration layer from discarding an LLM's improvement at the boundary.

## Current `src/agent.py` message flow

There are three distinct LLM interaction modes:

1. **One-shot query:** `query_llm_once()` sends an arbitrary system prompt and user message for metadata, negative packets, and Zeek integration generation.
2. **RFC selection:** `summarize_spec()` combines the summarization system prompt, full RFC user message, `select_intervals` schema/description, tool result, and a default tool-use nudge.
3. **Grammar refinement:** `run_refinement_loop()` receives the already assembled generation system message, sends the spec as the initial user message, exposes `validate_grammar` and optional RFC retrieval, and returns compiler/runtime feedback until success or the retry limit.

The refinement feedback is intentionally asymmetric:

- compilation failures ask for only corrected grammar;
- runtime failures include packet diagnostics and ask the model to validate again;
- successful packet tests explicitly ask for `TERMINATE`;
- no-packet cases allow termination after compilation;
- the best-scoring tested grammar is retained if full success is not reached.

## Repeated performance lessons encoded in the prompts

Across all three locations, the history records a consistent set of lessons:

- **Make tool order unavoidable.** Repeated prose nudges helped, but a single `validate_grammar` tool was more robust.
- **Separate syntax from behavior.** Compilation feedback alone produced grammars that looked valid but parsed or rejected packets incorrectly.
- **Give exact counterexamples.** Ambiguous switches, anonymous bitfields, whole-packet `&eod`, incorrect array units, and invented endian syntax are corrected with “wrong/correct” patterns.
- **State output contracts at the top and bottom.** Newer manuals constrain file count, root name, formatting, and unsupported syntax, then repeat critical checks at the end.
- **Model external context explicitly.** Parser arguments prevent models from inventing bytes for values that are configured or negotiated outside the packet.
- **Keep negative tests close to valid and layer-correct.** Single-fault raw payloads make runtime feedback actionable.
- **Prefer source-preserving context reduction.** RFC intervals are selected verbatim rather than summarized into potentially lossy prose.
- **Treat recovery as dangerous.** Synchronization is permitted only at real, distinctive protocol boundaries and must be confirmed after trial parsing.

## History caveats

- Commit `11412ef` has an author date of 2026-08-03 but a committer date of 2026-08-14; it appears late in the current branch topology. Both dates are noted above to avoid implying that the Vest prompt was present on `main` on August 3.
- Two prompt-related hashes appeared duplicated when querying `--all`, apparently through parallel/cherry-picked history. This report follows the commits reachable from current `main` and lists each effective change once.
- Some inferred motivations come from the exact invalid pattern named in the amendment rather than from an issue tracker. Those interpretations are phrased as “addresses,” “suggests,” or “likely.”
