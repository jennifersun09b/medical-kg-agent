"""
Model configurations for the 6 Chinese LLM providers.
Each entry maps a friendly model name to its provider key and model ID.

New field:
    search_engine (optional): None (default), one engine name, or a list of
        engine names. Valid names: "tavily" (international), "doubao"
        (Chinese). When set, search results are retrieved and injected into
        the prompt.

            "search_engine": None                    # plain model
            "search_engine": "tavily"                # one engine
            "search_engine": ["tavily", "doubao"]     # both, merged

        With a list, every engine is queried and the results are merged and
        de-duplicated by URL, so each engine contributes up to 3 rows.
"""

MODELS = {
    "qianwen": {
        "display_name": "千问 (Qwen)",
        "provider": "qianwen",
        # Qwen's latest long-context chat model on 百炼
        "model_id": "qwen-plus",
        "search_engine": "doubao",  # Set to "tavily" or "doubao" to enable search
    },
    "doubao": {
        "display_name": "豆包 (Doubao)",
        "provider": "doubao",
        # ByteDance Ark. Model must be activated on the account
        # (Ark console → 开通管理); an ep-xxxx endpoint ID also works here.
        "model_id": "doubao-seed-2-1-pro-260628",
        # This is a REASONING model. Left on, it burns ~3,900 reasoning tokens
        # per call on top of the answer (~75s/question), and `max_tokens` caps
        # only the VISIBLE output — so a low cap truncates the reply mid-sentence
        # while still paying for the full reasoning pass.
        # Disabled here so it matches the other five non-reasoning models and
        # runs ~2x faster for ~1/4 the tokens. Delete `extra_body` to re-enable
        # (and keep max_tokens >= 2048 if you do, or answers get cut off).
        "max_tokens": 2048,
        "extra_body": {"thinking": {"type": "disabled"}},
        "search_engine": "doubao",  # Set to "tavily" or "doubao" to enable search
    },
    "zhipu": {
        "display_name": "智谱 (GLM)",
        "provider": "zhipu",
        "model_id": "glm-4-plus",
        "search_engine": "doubao",  # Set to "tavily" or "doubao" to enable search
    },
    "kimi": {
        "display_name": "Kimi (Moonshot)",
        "provider": "kimi",
        "model_id": "moonshot-v1-32k",  # upgraded from 8k — injected search context exceeds 8k window
        "search_engine": "doubao",  # doubao only (tavily reserved for qianwen)
    },
    "deepseek": {
        "display_name": "DeepSeek",
        "provider": "deepseek",
        "model_id": "deepseek-chat",
        "search_engine": "doubao",  # Set to "tavily" or "doubao" to enable search
    },
    "hunyuan": {
        "display_name": "混元 (Hunyuan)",
        "provider": "hunyuan",
        # Tencent's flagship Hunyuan model on the TokenHub platform.
        # TokenHub also gateways GLM/Kimi/DeepSeek/Qwen, but we deliberately
        # use hy3 here so this slot tests Tencent's own model rather than
        # duplicating a provider we already call directly.
        "model_id": "hy3",
        "search_engine": "doubao",  # doubao only (tavily reserved for qianwen)
    },
}
