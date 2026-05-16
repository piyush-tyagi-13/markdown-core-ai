# mdcore Backends

mdcore (markdowncore-ai) is LLM-agnostic. You choose a backend for LLM inference and a separate backend for embeddings. This document covers every supported backend, how to install it, configure it, and what breaks when it is unavailable.

---

## Table of Contents

- [ollama](#ollama)
- [openai](#openai)
- [anthropic](#anthropic)
- [gemini](#gemini)
- [huggingface](#huggingface)
- [aggregator](#aggregator)
- [Mixing backends](#mixing-backends)
- [Fallback configuration](#fallback-configuration)

---

## ollama

**Status:** Core dependency - always installed with `markdowncore-ai`. No extras needed.

### Installation

```bash
# Install Ollama itself
curl -fsSL https://ollama.com/install.sh | sh

# Pull recommended models
ollama pull qwen3.5:4b
ollama pull phi4-mini
ollama pull nomic-embed-text
```

mdcore uses `ChatOllama` from `langchain_ollama` and `OllamaEmbeddings` from the same package. Both are bundled in core.

### Config YAML

```yaml
llm:
  backend: ollama
  model: qwen3.5:4b
  synthesise_model: phi4-mini
  temperature: 0.2
  think: false
  max_tokens: 1000
  timeout_seconds: 30

embeddings:
  backend: ollama
  local_model: nomic-embed-text
```

**Config field mapping:**

| mdcore field | Ollama param |
|---|---|
| `model` | model name |
| `max_tokens` | `num_predict` |
| `timeout_seconds` | `request_timeout` |
| `think` | think (extended reasoning) |
| `temperature` | temperature |

### Recommended models

| Role | Model | Why |
|---|---|---|
| Primary LLM | `qwen3.5:4b` | Good reasoning quality at mid-tier size, fast on consumer hardware |
| Synthesis LLM | `phi4-mini` | Lightweight, fast for final answer generation |
| Embeddings | `nomic-embed-text` | Default; strong retrieval quality, widely available |

### Known limitations

- `think` is **force-set to `false`** during synthesis regardless of your config. This prevents thinking tokens from consuming the token budget when generating the final answer.
- Requires the Ollama daemon to be running locally. It is not managed by mdcore.
- Performance depends entirely on your hardware (RAM, GPU VRAM).

### What breaks if unavailable

- **LLM:** `ChatOllama.invoke()` raises a connection error if the Ollama daemon is not running.
- **Embeddings:** `EmbeddingEngine` raises at `embed_texts` / `embed_query` time if Ollama is unreachable. An index built with Ollama embeddings cannot be queried with a different embedding backend.

---

## openai

**Status:** Optional extra. Not installed by default.

### Installation

```bash
pip install 'markdowncore-ai[openai]'
# or with uv
uv tool install markdowncore-ai --with langchain-openai
```

Uses `ChatOpenAI` from `langchain_openai` and `OpenAIEmbeddings` from the same package.

### Config YAML

```yaml
llm:
  backend: openai
  model: gpt-4o-mini
  synthesise_model: gpt-4o-mini
  api_key: sk-...
  temperature: 0.2
  max_tokens: 1000
  timeout_seconds: 30

embeddings:
  backend: openai
  api_model: text-embedding-3-small
  api_key: sk-...
```

The `api_key` field is required unless `OPENAI_API_KEY` is set in your environment - `langchain-openai` picks up the env var automatically.

### Recommended models

| Role | Model | Why |
|---|---|---|
| LLM | `gpt-4o-mini` | Default; cost-effective with strong quality |
| LLM (higher quality) | `gpt-4o` | When accuracy matters more than cost |
| Embeddings | `text-embedding-3-small` | Default; fast and accurate, low cost |
| Embeddings (higher quality) | `text-embedding-3-large` | Better retrieval at higher cost |

### Known limitations

- Requires an OpenAI API key and active billing.
- API rate limits apply - high-volume indexing may hit limits.

### What breaks if unavailable

If `langchain-openai` is not installed, mdcore raises an `ImportError` with install instructions before any request is made:

```
Backend 'openai' (llm) requires packages that are not installed.
Fix: mdcore deps install or: pip install langchain-openai>=0.2
```

---

## anthropic

**Status:** Optional extra. Not installed by default.

### Installation

```bash
pip install 'markdowncore-ai[anthropic]'
```

Uses `ChatAnthropic` from `langchain_anthropic`.

### Config YAML

```yaml
llm:
  backend: anthropic
  model: claude-haiku-4-5
  api_key: sk-ant-...
  temperature: 0.2
  max_tokens: 1000
  timeout_seconds: 30

# Anthropic does not support embeddings - pair with openai or ollama
embeddings:
  backend: openai
  api_model: text-embedding-3-small
  api_key: sk-...
```

The `api_key` field is required unless `ANTHROPIC_API_KEY` is set in your environment.

### Recommended models

| Role | Model | Why |
|---|---|---|
| LLM | `claude-haiku-4-5` | Default; fast and cost-effective |
| LLM (higher quality) | `claude-sonnet-4-5` | Better reasoning at moderate cost |

### Known limitations

- **Embeddings are not supported.** Anthropic does not provide an embedding API. You must configure a different backend for embeddings (openai, ollama, or gemini are all valid choices).
- Requires an Anthropic API key.

### What breaks if unavailable

If `langchain-anthropic` is not installed, mdcore raises an `ImportError` with install instructions before any request is made.

---

## gemini

**Status:** Core dependency as of 1.3.3. The `[gemini]` extra still exists in `pyproject.toml` but is now redundant - `langchain-google-genai>=2` is already included in core deps.

### Installation

```bash
# Already installed with markdowncore-ai >= 1.3.3
# The [gemini] extra is a no-op but harmless:
pip install 'markdowncore-ai[gemini]'
```

Uses `ChatGoogleGenerativeAI` from `langchain_google_genai` for LLM and `GoogleGenerativeAIEmbeddings` from the same package for embeddings.

### Config YAML

```yaml
llm:
  backend: gemini
  model: gemini-2.0-flash
  synthesise_model: gemini-2.0-flash
  api_key: AIza...
  temperature: 0.2
  max_tokens: 1000
  timeout_seconds: 30

embeddings:
  backend: gemini
  api_model: models/text-embedding-004
  api_key: AIza...
```

**Config field mapping:**

| mdcore field | Gemini param |
|---|---|
| `api_key` | `google_api_key` |
| `max_tokens` | `max_output_tokens` |
| `timeout_seconds` | `request_timeout` |

### Recommended models

| Role | Model | Why |
|---|---|---|
| LLM | `gemini-2.0-flash` | Default; fast, capable, generous free tier |
| Embeddings | `models/text-embedding-004` | Default from init wizard; strong multilingual support |

### Known limitations

- Requires a Google API key from Google AI Studio.
- Free tier has rate limits; high-volume use may require billing enabled.
- The `[gemini]` extra is now a no-op but does not cause errors.

### What breaks if unavailable

Failures surface as API errors at runtime (invalid key, quota exceeded) rather than import errors, since `langchain-google-genai` is a core dependency.

---

## huggingface

**Status:** Optional extra. Embeddings only - the LLM backend is broken.

### Installation

```bash
pip install 'markdowncore-ai[huggingface]'
# Installs langchain-huggingface and sentence-transformers
```

### Config YAML

```yaml
# Only use huggingface for embeddings - LLM is broken
llm:
  backend: ollama   # use any working LLM backend
  model: qwen3.5:4b

embeddings:
  backend: huggingface
  local_model: all-MiniLM-L6-v2
```

Models are downloaded to `~/.cache/huggingface` on first use.

### Recommended models

| Role | Model | Why |
|---|---|---|
| Embeddings | `all-MiniLM-L6-v2` | Default; small, fast, good quality |
| Embeddings (higher quality) | `BAAI/bge-large-en-v1.5` | State-of-the-art retrieval |
| Embeddings (multilingual) | `paraphrase-multilingual-MiniLM-L12-v2` | Multi-language vaults |

### Known limitations

**The LLM backend is broken.** `_build_llm()` in `llm_layer.py` has no `"huggingface"` case in its match statement and falls through to:

```
ValueError: Unknown LLM backend: huggingface
```

Do not set `llm.backend: huggingface`. Use `embeddings.backend: huggingface` only.

HuggingFace embedding models run locally via `sentence-transformers`. First load downloads the model - subsequent loads use the cache. CPU inference is significantly slower than Ollama on capable hardware.

Use this backend when you want local embeddings without running the Ollama daemon.

### What breaks if unavailable

- **Embeddings:** `ImportError` if `langchain-huggingface` or `sentence-transformers` is not installed.
- **LLM:** `ValueError` always - the backend is unimplemented regardless of installation state.

---

## aggregator

**Status:** Optional extra. NOT included in `[all]`. Must be installed separately.

The aggregator backend uses `llm-keypool` to round-robin across multiple free-tier LLM API keys (Groq, Cerebras, Mistral, OpenRouter). This lets you exceed individual provider rate limits by spreading load across keys.

### Installation

```bash
pip install 'markdowncore-ai[aggregator]'
# or install llm-keypool directly
pip install llm-keypool
```

mdcore detects availability by attempting to import `llm_keypool`. If the import fails, `check_backend("aggregator", "llm")` returns `installed=False`.

### Config YAML

```yaml
llm:
  backend: aggregator
  aggregator_category: general_purpose
  aggregator_rotate_every: 5
  temperature: 0.2
  max_tokens: 1000

# aggregator does not support embeddings - use ollama, openai, or gemini
embeddings:
  backend: ollama
  local_model: nomic-embed-text
```

**Config fields:**

| Field | Default | Description |
|---|---|---|
| `aggregator_category` | `general_purpose` | Key pool category to draw from |
| `aggregator_rotate_every` | `5` | Force key rotation after this many requests |

There is no `api_key` field in mdcore config for aggregator. Keys are managed entirely by `llm-keypool`'s SQLite database at `~/.llm-keypool/keys.db`.

### Registering API keys

Register one or more keys from any supported free-tier provider:

```bash
# Groq
llm-keypool add --provider groq --key <KEY> --model llama-3.3-70b-versatile --category general_purpose

# Cerebras
llm-keypool add --provider cerebras --key <KEY> --model llama-3.3-70b --category general_purpose

# Mistral
llm-keypool add --provider mistral --key <KEY> --model mistral-small-latest --category general_purpose

# OpenRouter (free tier models use the :free suffix)
llm-keypool add --provider openrouter --key <KEY> --model meta-llama/llama-3.3-70b-instruct:free --category general_purpose
```

View registered keys and active rotation state:

```bash
llm-keypool status
```

Keys are stored at `~/.llm-keypool/keys.db`. The `mdcore status` command shows the active key when the aggregator backend is configured.

### How round-robin rotation works

- `AggregatorChat` picks the next key in rotation from the SQLite pool.
- After `aggregator_rotate_every` requests, rotation is forced to the next key.
- 429 (rate limit) cooldown logic is handled inside `llm-keypool`, not mdcore.
- You can register keys from multiple providers in the same category - rotation spans all of them.

### Embeddings - explicitly blocked

The aggregator backend **cannot be used for embeddings**. Attempting to do so raises:

```
ValueError: aggregator backend does not support embeddings - embedding models cannot be
swapped mid-index. Use ollama, openai, or gemini for embeddings.
```

**Why:** ChromaDB requires all embeddings in a collection to share the same vector dimension. Different providers use different embedding models with different dimensions. If keys rotated across providers, the index would become silently corrupt. This restriction is intentional and permanent.

Always pair aggregator with a stable embedding backend: `ollama`, `openai`, or `gemini`.

### Recommended providers

| Provider | Model | Notes |
|---|---|---|
| Groq | `llama-3.3-70b-versatile` | Fast inference, generous free tier |
| Cerebras | `llama-3.3-70b` | Very fast on Cerebras silicon |
| Mistral | `mistral-small-latest` | Solid quality, European data residency |
| OpenRouter | `meta-llama/llama-3.3-70b-instruct:free` | Access to many models via one key |

Register keys from multiple providers for maximum throughput and resilience.

### Known limitations

- Key management is external to mdcore - use `llm-keypool` CLI.
- Not included in the `[all]` extra - must be installed explicitly.
- Embeddings are blocked by design.
- Free-tier providers have their own rate limits and availability SLAs.

### What breaks if unavailable

If `llm-keypool` is not installed, `assert_backend_available()` raises an `ImportError` before `_build_llm()` is called:

```
Backend 'aggregator' (llm) requires packages that are not installed.
Fix: mdcore deps install or: pip install llm-keypool
```

---

## Mixing backends

You can use different backends for LLM and embeddings independently:

```yaml
# Anthropic for LLM, OpenAI for embeddings
llm:
  backend: anthropic
  model: claude-haiku-4-5
  api_key: sk-ant-...

embeddings:
  backend: openai
  api_model: text-embedding-3-small
  api_key: sk-...
```

You can also use a different backend for synthesis than for ingestion:

```yaml
# ollama for ingestion LLM, aggregator for synthesis
llm:
  backend: ollama
  model: qwen3:8b
  synthesise_backend: aggregator
  aggregator_category: general_purpose

embeddings:
  backend: gemini
  api_model: models/text-embedding-004
  api_key: AIza...
```

**Hard constraint:** Once a vault is indexed with a given embedding backend and model, you cannot switch embedding backends without re-indexing from scratch. Vector dimensions must be consistent across the entire ChromaDB collection.

---

## Fallback configuration

Configure a fallback backend for resilience when the primary LLM is unavailable:

```yaml
llm:
  backend: ollama
  model: qwen3.5:4b
  fallback_backend: openai
  fallback_model: gpt-4o-mini
  fallback_api_key: sk-...
```

If the primary backend fails (connection error, timeout), mdcore retries with the fallback. The fallback only applies to LLM inference - embeddings do not have a fallback mechanism.

---

## Backend compatibility matrix

| Backend | LLM | Embeddings | Requires API key | Core dep |
|---|---|---|---|---|
| ollama | Yes | Yes | No | Yes |
| openai | Yes | Yes | Yes | No - `[openai]` extra |
| anthropic | Yes | No | Yes | No - `[anthropic]` extra |
| gemini | Yes | Yes | Yes | Yes (>=1.3.3) |
| huggingface | Broken | Yes | No | No - `[huggingface]` extra |
| aggregator | Yes | Blocked | Via llm-keypool | No - separate install |
