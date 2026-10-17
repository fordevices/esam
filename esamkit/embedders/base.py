"""
The Embedder interface.

An embedder turns text into a fixed-length list of floats (a "vector"). Texts
with similar MEANING end up with vectors that point in similar DIRECTIONS.
That's the whole trick — everything else in RAG is bookkeeping around it.

Two details every implementation must get right:

1. Documents and queries may need DIFFERENT treatment.
   Many retrieval models were trained with asymmetric inputs: a long passage
   on one side, a short question on the other. BGE v1.5, for example, wants a
   fixed instruction in front of the QUERY and nothing in front of passages.
   So the interface has two methods, not one.

2. Every vector must be traceable to the model that made it.
   Vectors from different models live in unrelated coordinate systems.
   Comparing them is meaningless — like comparing GPS coordinates with street
   addresses. `model_id` + `dim` are stored with each collection so a vector
   store can refuse to mix them.
"""

from abc import ABC, abstractmethod


class Embedder(ABC):
    #: Stable identity of the model, e.g. "BAAI/bge-base-en-v1.5".
    model_id: str
    #: Length of every vector this model produces, e.g. 768.
    dim: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed text that will be STORED (chunks of ICDs, SSDDs, SSRs...)."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a QUESTION that will be used to search stored chunks."""

    def describe(self) -> str:
        return f"{type(self).__name__}(model_id={self.model_id!r}, dim={self.dim})"
