"""
esamkit — small, swappable building blocks for the ESAM learning lessons.

The package is split into three kinds of parts, each behind an interface so
the lessons never care which concrete backend is in use:

    embedders/      text  -> vector        (local sentence-transformers, or an
                                            OpenAI-compatible /v1/embeddings)
    vectorstores/   store + search vectors (PostgreSQL + pgvector: local Docker
                                            here, VM-1 in the pilot)
    chunking.py     long document -> retrieval-sized pieces

`factory.py` reads `config.py` (environment variables) and hands back the
right concrete objects. To move from this laptop to the ESAM pilot you change
environment variables, not code.
"""
