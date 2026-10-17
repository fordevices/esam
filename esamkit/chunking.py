"""
Chunking — cutting documents into retrieval-sized pieces.

Why chunk at all?
  An embedding is ONE vector for a whole input. Embed a 40-page ICD as one
  vector and every topic in it gets averaged into mush: the vector is "about"
  everything, so it's close to nothing in particular. Small chunks keep each
  vector about one thing. Embedding models also have an input limit
  (BGE: 512 tokens, roughly 2,000 characters) and silently cut off anything
  longer.

Why overlap?
  A sentence that straddles a cut would otherwise be split in half and
  belong properly to neither chunk. Overlapping by a little repeats the
  boundary text in both.

This chunker is deliberately simple but avoids two flaws of the earlier
character slicer:
  1. It breaks on paragraph / sentence / word boundaries, never mid-word.
  2. It keeps the nearest markdown heading with each chunk, so a chunk like
     "Transmission rate is 100 ms" still says WHICH section it came from.
"""

import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _split_long(text: str, size: int) -> list[str]:
    """Split one oversized paragraph at sentence, then word, boundaries."""
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(current) + len(sentence) + 1 <= size:
            current = f"{current} {sentence}".strip()
            continue
        if current:
            pieces.append(current)
        # A single sentence longer than `size`: fall back to word boundaries.
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        current = sentence
    if current:
        pieces.append(current)
    return pieces


def chunk_markdown(text: str, size: int = 600, overlap: int = 80) -> list[str]:
    """Return chunks of at most ~`size` characters, each prefixed with its
    section heading, with ~`overlap` characters repeated between neighbours."""
    # 1. Walk the document, remembering the current heading for each paragraph.
    #    The label is "document title › section", so a chunk from section
    #    "2. Task set" still says WHICH document's task set it is. Without the
    #    title, that chunk's vector knows nothing about "brake ECU".
    blocks: list[tuple[str, str]] = []  # (heading, paragraph)
    title, heading = "", ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        m = _HEADING.match(para.splitlines()[0])
        if m:
            if len(m.group(1)) == 1:          # "# Title" -> document title
                title = heading = m.group(2).strip()
            else:                             # "## Section" and deeper
                heading = f"{title} › {m.group(2).strip()}" if title else m.group(2).strip()
            rest = "\n".join(para.splitlines()[1:]).strip()
            if not rest:
                continue
            para = rest
        for piece in (_split_long(para, size) if len(para) > size else [para]):
            blocks.append((heading, piece))

    # 2. Pack paragraphs into chunks, starting a new chunk when the section
    #    changes or the size limit would be exceeded.
    chunks: list[str] = []
    cur_heading, cur_body = None, ""
    for h, para in blocks:
        if cur_body and (h != cur_heading or len(cur_body) + len(para) + 2 > size):
            chunks.append(_render(cur_heading, cur_body))
            # Overlap: carry the tail of the previous chunk (whole words only)
            # into the next one — but only within the same section.
            tail = cur_body[-overlap:] if h == cur_heading else ""
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            cur_body = f"…{tail}\n\n{para}" if tail else para
        else:
            cur_body = f"{cur_body}\n\n{para}" if cur_body else para
        cur_heading = h
    if cur_body:
        chunks.append(_render(cur_heading, cur_body))
    return chunks


def _render(heading: str | None, body: str) -> str:
    return f"[{heading}]\n{body}" if heading else body
