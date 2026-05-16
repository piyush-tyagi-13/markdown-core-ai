## mdcore/llm/ — LLM Layer Module

### Purpose
Single abstraction over all supported LLM backends. Exposes four high-level methods used by the rest of mdcore: classify, propose, synthesise, route_folder. Handles backend construction, fallback, token logging, and LangSmith tracing setup.

### Public interface

**`LLMLayer(cfg: LLMConfig)`**
- `__init__`: builds nothing immediately (lazy). Sets LangSmith env vars if `cfg.langsmith_api_key` is set.
- `classify(summary: str, candidates: list[Document]) -> ClassificationResult`
- `propose(classification: ClassificationResult, existing_content: str, incoming_summary: str) -> str`
- `synthesise(query: str, raw_context: str) -> str`
- `route_folder(document: str, folders: list[str], descriptions: dict | None) -> FolderRoutingResult`

**`ClassificationResult`** (dataclass)
- action: str — "update" | "new"
- target_file: Optional[str]
- reasoning: str
- confidence: float

**`FolderRoutingResult`** (dataclass)
- folder: str
- confidence: float
- reasoning: str

### _build_llm(backend, model, api_key, cfg) -> BaseChatModel

Match statement over backend:
- `"ollama"` -> `ChatOllama(model, temperature, num_predict=max_tokens, think, request_timeout)`
- `"openai"` -> `ChatOpenAI(model, api_key, temperature, max_tokens, timeout)`
- `"anthropic"` -> `ChatAnthropic(model, api_key, temperature, max_tokens, timeout)`
- `"gemini"` -> `ChatGoogleGenerativeAI(model, google_api_key=api_key, temperature, max_output_tokens, request_timeout)`
- `"aggregator"` -> `AggregatorChat(category, rotate_every)` from llm_keypool
- `"huggingface"` -> NOT HANDLED — falls to `raise ValueError("Unknown LLM backend: huggingface")`. BUG: huggingface is listed in _LLMBackend Literal and config but crashes here.
- `_` (unknown) -> `raise ValueError`

All backends: `assert_backend_available(backend, "llm")` called before import to give actionable error message.

### _invoke(prompt: str) -> str

Lazy-initializes self._llm on first call.
1. `self._get_llm().invoke(prompt)` -> `response.content`
2. If content is empty string: raises `RuntimeError("LLM returned an empty response. Ollama may be under load — try again, or check 'ollama ps'.")`
3. If any exception: log warning, try `self._get_fallback()`
4. If fallback also fails or not configured: raises `RuntimeError(f"LLM call failed and no fallback configured.\nError: {primary_err}")`

Fallback is lazy-initialized in `_get_fallback()` using `cfg.fallback_backend`, `cfg.fallback_model`, `cfg.fallback_api_key`.

### classify() method

Prompt contains:
- 4 rules (prefer NEW for self-contained docs, only UPDATE for continuation, similarity != update, when in doubt -> NEW)
- INCOMING DOCUMENT first 800 chars
- CANDIDATE FILES block: each `"FILE: {source_file}\n{page_content[:400]}"` — GOTCHA: `page_content` is the source_file string (from ClassificationEngine bug), not actual content
- Expected response format: `ACTION: update|new\nTARGET: path or none\nCONFIDENCE: 0.0-1.0\nREASONING: sentence`

`_parse_classification(raw)`: splits on `:`, lowercases keys, defaults action to "new" if invalid, defaults confidence to 0.7 if parse fails, target=None if "none" or empty.

### propose() method

Prompt contains:
- `ACTION: UPDATE {target_file}` or `CREATE a new file`
- `CONFIDENCE: N`
- `EXISTING FILE CONTENT (truncated)`: `existing_content[:600]`
- `INCOMING SUMMARY`: `incoming_summary[:800]`
- Instruction: 2-4 bullet points, specific, no headers

Returns: raw string from _invoke(). Caller (ProposalGenerator) wraps it in Proposal dataclass.

### synthesise() method

Three synthesis paths (checked in order):
1. `synth_backend == "ollama" AND synth_model` set: builds dedicated `ChatOllama(model=synth_model, think=False, temperature=0)` — think is FORCED False for synthesis
2. `synth_backend != primary backend OR synth_model != primary model`: builds dedicated `_build_llm(synth_backend, synth_model or primary_model, synth_api_key, cfg)`
3. Else: uses `self._invoke(prompt)` (primary LLM)

After LLM response: `_strip_hallucinated_citations(briefing, raw_context)`
- Counts source blocks `[N]` at line start in raw_context
- Removes any `[N]` citation in briefing where N > source_count

### route_folder() method

Prompt contains:
- 4 rules (most specific folder, closest if no match, no invented folders, name = signal)
- INCOMING DOCUMENT first 600 chars
- VAULT FOLDERS: each `"  {folder}" + (" — {desc}" if description exists)`
- Expected: `FOLDER: exact path\nCONFIDENCE: N\nREASONING: sentence`

`_parse_folder_routing(raw, valid_folders)`: validates folder against valid_folders list, case-insensitive fallback, defaults to valid_folders[0] if no match. Defaults confidence to 0.7 on parse failure.

### LangSmith wiring

`LLMLayer.__init__()` sets these env vars (before any LangChain object is created):
```python
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_API_KEY"] = cfg.langsmith_api_key
os.environ["LANGCHAIN_PROJECT"] = cfg.langsmith_project or "mdcore"
```
LangChain picks these up automatically. All LLM calls (including synthesis builds) are traced. No explicit tracing code elsewhere.

### _log_tokens(label, response)

Normalizes token usage from `response.response_metadata`:
- Gemini: `usage_metadata.prompt_token_count` / `candidates_token_count` (or `input_tokens`/`output_tokens` fallback)
- OpenAI: `token_usage.prompt_tokens` / `completion_tokens`
- Anthropic: `usage.input_tokens` / `output_tokens`
- Ollama: `prompt_eval_count` / `eval_count`
- llm-keypool: `tokens_used` (combined only, so returns 0, tokens_used)
- Logged at INFO if nonzero, DEBUG if unavailable

### Side effects
- LLM API calls (Ollama, OpenAI, Anthropic, Gemini, llm-keypool)
- Sets OS env vars for LangSmith (if configured)
- Logs token usage to rotating log file

### Gotchas
- huggingface backend causes ValueError — do not configure `llm.backend: huggingface`
- Synthesis path 1 (Ollama dedicated synth model) always uses temperature=0, ignoring cfg.temperature
- Synthesis path 2/3 uses cfg.temperature (0.2 default) — inconsistency between paths
- Empty response raises RuntimeError, not a retry. If Ollama is overloaded, user must re-run.
- Fallback LLM is built lazily — deps not checked until primary fails
- _parse_classification defaults to confidence=0.7 on parse error — not surfaced to user as a parse failure
