#!/usr/bin/env python3
"""
Lesson 02 — What an embedding actually is, worked by hand.

    python lessons/02_embeddings/embeddings_by_hand.py

Five steps, each printed as it runs:

  STEP 1  Turn one sentence into a vector and look at the raw numbers.
  STEP 2  Compute cosine similarity BY HAND (a for-loop), then with numpy.
  STEP 3  A similarity table: do meanings cluster the way an engineer expects?
  STEP 4  Meaning vs. shared words: "lock" in brakes vs. "lock" on a door.
  STEP 5  Query vs. passage prefixes: right, none, and wrong (E5-style, as in
          the earlier embeddings.py) — and what it does to search ranking.

No vector database yet. That's lesson 03. Here we only need the embedder.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from esamkit.factory import get_embedder  # noqa: E402


def banner(title: str) -> None:
    print("\n" + "═" * 78 + f"\n{title}\n" + "═" * 78)


# ─────────────────────────────────────────────────────────────────────────────
# Cosine similarity, written out longhand so nothing is hidden.
#
#   cos(a, b) = (a · b) / (|a| × |b|)
#
#   a · b  = sum of a[i] * b[i]       ("dot product": how much they agree)
#   |a|    = sqrt(sum of a[i]^2)      (length of the arrow)
#
# Result is between -1 and 1:
#    1.0  -> arrows point the same way (same meaning)
#    0.0  -> at right angles (unrelated)
#   -1.0  -> opposite (rare in practice for text models; most scores land
#            between about 0.3 and 1.0 — so judge scores RELATIVE to each
#            other, never against an absolute threshold)
# ─────────────────────────────────────────────────────────────────────────────
def cosine_by_hand(a: list[float], b: list[float]) -> float:
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def main() -> None:
    emb = get_embedder()
    print(f"Embedder: {emb.describe()}")

    # ── STEP 1 ──────────────────────────────────────────────────────────────
    banner("STEP 1 — one sentence becomes a list of numbers")
    sentence = "The brake ECU broadcasts EBC1 every 100 ms."
    vec = emb.embed_documents([sentence])[0]
    print(f'Sentence : "{sentence}"')
    print(f"Length   : {len(vec)} numbers (the model's fixed dimension)")
    print(f"First 8  : {[round(x, 4) for x in vec[:8]]}")
    print(f"Min/Max  : {min(vec):.4f} / {max(vec):.4f}")
    print(f"|vec|    : {math.sqrt(sum(x * x for x in vec)):.6f}")
    print("""
What you're looking at:
  * No single number "means" brake or ECU. Meaning is spread across all
    768 together, the way a colour is a mix of R, G and B, just with 768
    channels instead of 3.
  * |vec| is 1.000000 because we normalize every vector. All arrows have
    the same length, so only DIRECTION carries meaning, and cosine
    similarity reduces to a plain dot product.
  * The same sentence always gives the same vector, so ingestion is repeatable.""")

    # ── STEP 2 ──────────────────────────────────────────────────────────────
    banner("STEP 2 — cosine similarity by hand vs. numpy")
    a_txt = "ABS is active on the front axle."
    b_txt = "Anti-lock braking is engaged on the front wheels."
    a, b = emb.embed_documents([a_txt, b_txt])
    by_hand = cosine_by_hand(a, b)
    import numpy as np
    by_numpy = float(np.dot(a, b))  # vectors are normalized -> dot == cosine
    print(f'A: "{a_txt}"\nB: "{b_txt}"')
    print(f"cosine by hand : {by_hand:.6f}")
    print(f"numpy dot      : {by_numpy:.6f}   (same number, about 1000x faster)")
    print("\nThe two sentences share almost no words (ABS vs anti-lock, axle vs wheels),\n"
          "yet the score is high. That is the point of embeddings: similar MEANING,\n"
          "not similar spelling.")

    # ── STEP 3 ──────────────────────────────────────────────────────────────
    banner("STEP 3 — similarity table: brakes vs. display vs. engine")
    labels = ["S1", "S2", "S3", "S4", "S5", "S6"]
    sentences = [
        "ABS active: brake pressure is being modulated to prevent wheel lock-up.",  # brakes
        "Wheel-slip control releases hydraulic pressure before a wheel locks.",      # brakes
        "Amber ABS telltale illuminates on the driver display.",                     # display<->brakes
        "NVG mode dims the cluster so goggles are not saturated.",                   # display
        "Blackout mode suppresses all non-critical graphics at night.",              # display
        "Engine coolant temperature is reported every second.",                      # engine
    ]
    vecs = np.array(emb.embed_documents(sentences))
    sim = vecs @ vecs.T          # every pair's dot product in one matrix multiply
    for lab, s in zip(labels, sentences):
        print(f"  {lab}: {s}")
    print("\n       " + "  ".join(f"{l:>5}" for l in labels))
    for i, lab in enumerate(labels):
        row = "  ".join(f"{sim[i, j]:5.2f}" for j in range(len(labels)))
        print(f"  {lab}  {row}")
    print("""
How to read it:
  * The diagonal is 1.00 (each sentence compared with itself).
  * Compare the S1-S2 score with the S1-S6 score.
  * S3 (ABS telltale on the display) mentions both brakes and the display, so it
    should score fairly well against BOTH groups. Embeddings handle "partly
    about X" naturally.""")

    # ── STEP 4 ──────────────────────────────────────────────────────────────
    banner('STEP 4 — same word, different meaning: "lock"')
    anchor = "The ABS prevents the wheels from locking under hard braking."
    probes = [
        "Anti-skid control keeps tyres rolling during an emergency stop.",  # same meaning, no shared key word
        "The crew door lock actuator reported a fault.",                    # shares "lock", different meaning
        "Wheel lock-up was observed on gravel during testing.",             # shares "lock" AND meaning
    ]
    av = emb.embed_documents([anchor])[0]
    pv = emb.embed_documents(probes)
    print(f'Anchor: "{anchor}"\n')
    for p, v in sorted(zip(probes, pv), key=lambda t: -float(np.dot(av, t[1]))):
        print(f"  {float(np.dot(av, v)):.3f}  {p}")
    print("""
A keyword search for "lock" would rank the door-lock sentence at the top and
miss the anti-skid sentence entirely. An embedding ranks by meaning, though
shared words still pull a little: the door-lock line isn't scored at zero.
This is why production systems often combine both kinds of search ("hybrid").""")

    # ── STEP 5 ──────────────────────────────────────────────────────────────
    banner("STEP 5 — query prefixes: right vs. none vs. wrong")
    question = "What should the display do if brake data stops arriving?"
    passages = [
        # the correct answer
        "If no EBC1 frame arrives for 300 ms, the cluster treats anti-lock braking as "
        "unavailable and lights the ABS telltale with a BRAKE DATA LOST message.",
        # related but wrong: the ECU's own fault behaviour
        "On any internal fault the ECU drops to conventional braking and requests the "
        "amber warning lamp.",
        # display topic, wrong subject
        "The cluster must show critical telltales within 2 seconds of power-on.",
        # unrelated
        "The unit operates from the 28 V vehicle bus per MIL-STD-1275.",
    ]
    bge_q = "Represent this sentence for searching relevant passages: "
    variants = {
        # What esamkit does (BGE model card): instruction on query, nothing on passages.
        "correct (BGE instruction on query)": (emb.embed_query(question), emb.embed_documents(passages)),
        # No instruction at all.
        "no prefixes": (emb.embed_raw([question])[0], emb.embed_raw(passages)),
        # What the earlier embeddings.py did: E5 conventions on a BGE model.
        "wrong (E5 'query:'/'passage:')": (emb.embed_raw(["query: " + question])[0],
                                           emb.embed_raw(["passage: " + p for p in passages])),
    }
    print(f'Question: "{question}"\n')
    for i, p in enumerate(passages):
        print(f"  P{i}: {p[:90]}{'…' if len(p) > 90 else ''}")
    print("\n  variant                               P0     P1     P2     P3    gap(P0 - best other)")
    for name, (q, ps) in variants.items():
        scores = [float(np.dot(q, p)) for p in ps]
        gap = scores[0] - max(scores[1:])
        print(f"  {name:36s}" + "".join(f"{s:7.3f}" for s in scores) + f"   {gap:+.3f}")
    print(f"""
P0 is the right answer. Look at the GAP column: how far ahead of the
runner-up the right answer is. A bigger gap means retrieval is more robust;
a gap near zero means the right chunk could easily be pushed out of the
top-k by a slightly different wording.

With four hand-picked passages all three variants may still rank P0 first.
On a real corpus of thousands of chunks, small losses in the gap become
missed retrievals. The instruction is literally "{bge_q.strip()}". The
model was trained with it, so we use it.""")


if __name__ == "__main__":
    main()
