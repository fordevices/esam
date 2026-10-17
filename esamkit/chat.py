"""
Chat model client: any OpenAI-compatible /v1/chat/completions endpoint.

On the pilot this is the IL5 API Gateway (GPT-5.1). Configure:
    ESAM_CHAT_BASE_URL=https://<gateway>/v1
    ESAM_CHAT_API_KEY=...
    ESAM_CHAT_MODEL=<the model name the gateway exposes>

If those aren't set, `chat_configured()` is False and lesson 05 runs in
"dry run" mode: it prints the exact prompt it would have sent. That's useful
on its own. Most of what makes RAG good or bad is visible in the prompt.
"""

from .config import Settings, settings as default_settings


def chat_configured(s: Settings = default_settings) -> bool:
    return bool(s.chat_base_url and s.chat_model)


def complete(system: str, user: str, s: Settings = default_settings) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=s.chat_base_url, api_key=s.chat_api_key or "unused")
    resp = client.chat.completions.create(
        model=s.chat_model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content
