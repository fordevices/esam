# Lesson 02: What an embedding is, worked by hand

```bash
python lessons/02_embeddings/embeddings_by_hand.py
```

No database yet, only the model. All numbers below are real output from `BAAI/bge-base-en-v1.5`
on CPU. Your run gives the same numbers: same model plus same text means the same vector.

## Step 1: a sentence becomes 768 numbers

```
Sentence : "The brake ECU broadcasts EBC1 every 100 ms."
Length   : 768
First 8  : [-0.003, -0.0164, 0.0543, -0.0031, -0.0073, -0.0153, 0.0433, 0.0448]
|vec|    : 1.000000
```

No single number means "brake". Think of the vector as an **arrow in 768-dimensional space**.
We normalize every arrow to length 1, so the only thing that differs between two texts is the
**direction** their arrows point.

## Step 2: cosine similarity, by hand

```
cos(a, b) = (a · b) / (|a| × |b|)        and with |a| = |b| = 1, that's just a · b
```

| A | B | cosine |
|---|---|---|
| ABS is active on the front axle. | Anti-lock braking is engaged on the front wheels. | **0.834** |

Almost no shared words, yet a high score. The for-loop in the script and `numpy.dot` give the same
number; numpy is simply faster.

## Step 3: does meaning cluster the way an engineer expects?

```
          S1     S2     S3     S4     S5     S6
  S1   1.00   0.77   0.67   0.54   0.48   0.60      S1 ABS modulating pressure (brakes)
  S2   0.77   1.00   0.50   0.49   0.43   0.55      S2 wheel-slip control      (brakes)
  S3   0.67   0.50   1.00   0.59   0.56   0.58      S3 ABS telltale on display (both)
  S4   0.54   0.49   0.59   1.00   0.68   0.43      S4 NVG dimming             (display)
  S5   0.48   0.43   0.56   0.68   1.00   0.42      S5 blackout mode           (display)
  S6   0.60   0.55   0.58   0.43   0.42   1.00      S6 engine coolant temp     (engine)
```

What the real numbers teach:

1. **Clusters exist.** Brakes S1–S2 = 0.77; display S4–S5 = 0.68. Both are the strongest pairs
   outside the diagonal.
2. **"Both" topics sit in between.** S3 (an ABS lamp on the display) scores 0.67 with brakes and
   0.59 with NVG.
3. **Scores are compressed.** Nothing here drops below 0.42, even "engine coolant" vs "NVG"
   (0.43). BGE scores everything in roughly the 0.4–1.0 range. Never use an absolute threshold
   like "0.5 = relevant". **Only the ranking means something.**
4. **Surprises happen.** Engine coolant (S6) scores *higher* against S1 (0.60) than NVG does
   (0.54). Both S1 and S6 are "a vehicle system reporting a status", and the model picks up on
   that. Embeddings capture *many* kinds of similarity, not only the one you had in mind.

## Step 4: same word, different meaning

Anchor: *"The ABS prevents the wheels from locking under hard braking."*

| Score | Probe | Shares "lock"? | Same meaning? |
|---|---|---|---|
| **0.769** | Anti-skid control keeps tyres rolling during an emergency stop. | no | yes |
| 0.750 | Wheel lock-up was observed on gravel during testing. | yes | yes |
| 0.644 | The crew door lock actuator reported a fault. | yes | **no** |

A keyword search would rank the door-lock sentence high and miss the anti-skid one entirely. The
embedding puts the anti-skid sentence **first, without the word "lock"**. The door-lock sentence
still scores 0.644, though: shared words pull a little. This is why production systems often
combine keyword and vector search ("hybrid search").

## Step 5: the prefix mistake in the old `embeddings.py`, measured

Question: *"What should the display do if brake data stops arriving?"*. P0 is the right passage.

| Variant | P0 (right) | P1 | P2 | P3 (unrelated) | **Gap** (P0 − best other) |
|---|---|---|---|---|---|
| **Correct**: BGE instruction on the query only (esamkit) | 0.658 | 0.522 | 0.445 | 0.385 | **+0.136** |
| No prefixes at all | 0.687 | 0.558 | 0.490 | 0.427 | +0.129 |
| **Wrong**: E5-style `query:` / `passage:` (old script) | 0.692 | 0.590 | 0.526 | 0.521 | **+0.102** |

1. All three rank P0 first. With four passages the mistake is invisible, which is exactly why it
   went unnoticed.
2. The wrong prefixes cut the right answer's lead by **25%** (0.136 → 0.102).
3. They raise *everything*. The unrelated 28 V power passage jumps from 0.385 to 0.521, because
   every passage now starts with the same word, `passage:`, which makes them all look alike.
   Less separation means that on a corpus of thousands of chunks, the right one is more likely
   to slip out of the top k.
4. The raw scores were *higher* with the wrong prefix. **Higher scores aren't better retrieval;
   a bigger gap is.**

## Take-aways

* An embedding is a direction; similarity is the angle between directions.
* Rank, don't threshold. Scores from one model aren't comparable to another model's.
* Use the model the way it was trained: read its model card for prefixes.
* Changing models changes every vector, which is why the vector store records the model
  (lesson 03).
