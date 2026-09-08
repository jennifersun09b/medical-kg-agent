# Search Engine Integration Guide

## Overview

This enhancement adds search engine capabilities to the model response generation pipeline. You can now augment model responses with real-time search results from:
- **Tavily**: International search engine (for global medical literature)
- **Doubao Search**: Chinese search engine (for local Chinese medical resources)

## Setup

### 1. Install Dependencies

No additional dependencies are required beyond the existing `httpx` package.

### 2. Configure API Keys

Add your search engine API keys to `.env`:

```bash
# Tavily — International search engine
TAVILY_API_KEY=your_tavily_api_key_here

# Doubao Search — Chinese search engine
DOUBAO_SEARCH_API_KEY=your_doubao_search_api_key_here
```

### 3. Enable Search for Models

Edit `models.py` to enable search for specific models by setting the `search_engine` field:

```python
MODELS = {
    "qianwen": {
        "display_name": "千问 (Qwen)",
        "provider": "qianwen",
        "model_id": "qwen-plus",
        "search_engine": ["tavily", "doubao"],
    },
    # ... other models
}
```

**Options:**
- `None` or omit — no search augmentation (default)
- `"tavily"` — Tavily international search only
- `"doubao"` — Doubao Chinese search only
- `["tavily", "doubao"]` — both engines, results merged

With a list, every engine is queried and results are merged and
de-duplicated by URL, so each engine contributes up to `max_results` rows
(3 by default, giving up to 6 total). Engines are queried independently: if
one fails, the other's results are still used, and the failure is logged
rather than raised. Each result is tagged with its engine in the prompt, so
the model sees `[1]（tavily）…` / `[4]（doubao）…`.

## How It Works

1. **Question Analysis**: When a question is sent to a model with search enabled, the system first performs a search query using the question text.

2. **Search Results**: The top 3 search results are retrieved, including:
   - Title
   - Content snippet
   - Source URL

3. **Context Injection**: Search results are formatted and prepended to the user's question in Chinese:
   ```
   以下是相关的搜索结果，可作为参考：

   [1] <title>
   <content snippet>
   来源: <url>

   [2] ...

   问题: <original question>
   ```

4. **Model Response**: The model receives both the search context and the question, allowing it to reference current information in its answer.

## Usage Examples

### Run with search-enabled models:

```bash
# Run all models (some with search, some without)
python run_parallel.py

# Run specific models with search
python run_parallel.py --models qianwen doubao

# Resume with search
python run_parallel.py --resume

# Test with limited questions
python run_parallel.py --limit 10
```

### Output Format

Results now include four additional fields:
- `search_engine`: what was configured (string, list, or `null`)
- `search_used`: whether any search context reached the model
- `engines_used`: which engines actually returned results — this is the field
  to trust, since a configured engine can fail
- `search_hits`: how many result rows were injected

```json
{
    "row_id": 0,
    "final_qid": "Q001",
    "question": "...",
    "model": "qianwen",
    "response": "...",
    "search_engine": ["tavily", "doubao"],
    "search_used": true,
    "engines_used": ["tavily"],
    "search_hits": 3,
    "elapsed_s": 2.45
}
```

## Recommended Configuration

### For Medical Questions:

1. **Tavily for international models** (accessing global medical literature):
   - Recommended for: Qianwen, DeepSeek (if they handle English well)

2. **Doubao Search for Chinese-focused models** (accessing Chinese medical resources):
   - Recommended for: Doubao, Zhipu, Kimi, Hunyuan

3. **Mixed approach**: Enable search for 1-2 models per provider to compare search-augmented vs. plain model responses.

### Example Configuration:

```python
MODELS = {
    "qianwen": {
        # ... existing config
        "search_engine": "tavily",  # International search
    },
    "doubao": {
        # ... existing config
        "search_engine": "doubao",  # Chinese search
    },
    "zhipu": {
        # ... existing config
        "search_engine": None,  # Baseline without search
    },
    # etc.
}
```

## Error Handling

- If search fails (network error, API key missing, rate limit), the system logs a warning and continues with the original question without search context
- Models without search configured work exactly as before
- Search errors do not block model response generation

## Performance Considerations

- Each search query adds ~1-3 seconds to the request
- Search results are retrieved synchronously before the model call
- Failed searches fall back gracefully without adding delay

## Troubleshooting

### Search not working:
1. Verify API keys are set in `.env`
2. Check the `search_engine` field in `models.py` is `"tavily"`, `"doubao"`,
   or a list of both
3. Run `python test_search.py` to test each engine in isolation
4. Check `engines_used` in the output records — a configured engine can fail
   while the other still returns results

### Doubao Search — working

Both engines are verified live. Doubao uses the host in `DOUBAO_SEARCH_URL`
(`.env`), defaulting to `https://open.feedcoopapi.com/search_api/web_search`.
It is **not** an Ark endpoint, and `DOUBAO_SEARCH_API_KEY` is a separate
credential from `DOUBAO_API_KEY`.

Two traits of this API are worth knowing before you change `doubao_search()`:

**Request keys are PascalCase and case-sensitive.** `Query` + `SearchType`
work; lowercase `query`/`search_type` are silently ignored and come back as
`"query or search type is empty"`. `SearchType` must be lowercase `"web"` —
`"Web"` returns `"invalid search type"`. `Count` caps results server-side.

**It returns HTTP 200 even on failure.** Auth and parameter errors arrive as
`200` with `Result: null` and the detail in `ResponseMetadata.Error`, so
`raise_for_status()` alone cannot catch them. `doubao_search()` inspects that
field explicitly; without it, a bad key would look like "no results found"
rather than an error.

Response items expose `Title`, `Url`, and three text fields of increasing
length (`Snippet`, `Summary`, `Content`). We prefer `Content` and fall back
through the other two, since not every result carries all three.

## Files Modified

- **search_tools.py** (new): Search engine integration
- **models.py**: Added `search_engine` field to model configs
- **run_parallel.py**: Integrated search into parallel pipeline
- **run.py**: Integrated search into sequential pipeline
- **.env**: `TAVILY_API_KEY`, `DOUBAO_SEARCH_API_KEY`, `DOUBAO_SEARCH_URL`
- **test_search.py**: Per-engine and merged-mode checks
- **test_apis.py**: Hints for the two Doubao-search error messages
