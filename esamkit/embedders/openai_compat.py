"""
OpenAI-compatible embedder — for the day an embeddings endpoint exists.

Today the ESAM gateway only serves chat models, so this backend is unused.
It is here so that switching is a config change:

    ESAM_EMBED_BACKEND=openai
    ESAM_OPENAI_BASE_URL=https://<gateway>/v1
    ESAM_OPENAI_API_KEY=...
    ESAM_OPENAI_EMBED_MODEL=<model name the gateway exposes>

The same client works against self-hosted servers that speak the OpenAI
embeddings protocol (vLLM, Hugging Face TEI, Ollama).

REMEMBER: switching embedders means re-embedding EVERYTHING. The vector store
will refuse to mix vectors from a different model_id.
"""

from .base import Embedder


class OpenAICompatEmbedder(Embedder):
    def __init__(self, base_url: str, api_key: str, model: str):
        if not (base_url and model):
            raise ValueError(
                "ESAM_OPENAI_BASE_URL and ESAM_OPENAI_EMBED_MODEL must be set "
                "for ESAM_EMBED_BACKEND=openai"
            )
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key or "unused")
        self._model = model
        self.model_id = f"openai-compat:{model}"
        # Ask the server once for a vector so we learn the dimension instead
        # of hard-coding it (the old script hard-coded 768).
        self.dim = len(self._embed(["dimension probe"])[0])

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model, input=texts)
        # Responses carry an index; sort so output order matches input order.
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        # Hosted embedding APIs usually take care of query/document asymmetry
        # server-side (or the model is symmetric). If the server hosts BGE,
        # add the BGE query instruction here.
        return self._embed([text])[0]
