#!/usr/bin/env python3
"""
Lesson 00 — Stage the embedding model for an air-gapped machine.

Run this ONCE on a machine WITH internet:

    python lessons/00_setup/stage_model.py            # download + manifest
    python lessons/00_setup/stage_model.py --verify   # check files (run on BOTH sides)

What it does
------------
1. Downloads the model named by ESAM_EMBED_MODEL_ID (default
   BAAI/bge-base-en-v1.5) into ESAM_EMBED_MODEL_DIR (default
   esam/models/bge-base-en-v1.5).
2. Skips the extra copies some repos ship (ONNX, OpenVINO, TF, Flax) — we
   only need the PyTorch safetensors weights. Smaller media to carry across.
3. Writes MANIFEST.sha256 listing a SHA-256 hash of every file, so the
   receiving side can prove nothing was corrupted or altered in transit.
4. Loads the model from the folder with networking forced OFF, which proves
   the copy is self-contained before you rely on it on the other side.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

# Make `import esamkit` work when run as a plain script from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from esamkit.config import settings  # noqa: E402

MANIFEST = "MANIFEST.sha256"

# Only what sentence-transformers needs to run on CPU with PyTorch.
ALLOW = [
    "*.json",            # config, tokenizer config, sentence-transformers config
    "*.txt",             # vocab.txt
    "model.safetensors", # the weights (~440 MB for bge-base)
    "1_Pooling/*",       # sentence-transformers pooling layer config
    "README.md",         # model card: the license and usage notes travel with it
    "LICENSE*",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(model_dir: Path) -> None:
    files = sorted(p for p in model_dir.rglob("*")
                   if p.is_file() and p.name != MANIFEST and ".cache" not in p.parts)
    lines = [f"{sha256(p)}  {p.relative_to(model_dir)}" for p in files]
    (model_dir / MANIFEST).write_text("\n".join(lines) + "\n")
    total = sum(p.stat().st_size for p in files) / 1e6
    print(f"Wrote {MANIFEST}: {len(files)} files, {total:.0f} MB")


def verify(model_dir: Path) -> bool:
    manifest = model_dir / MANIFEST
    if not manifest.exists():
        print(f"No {MANIFEST} in {model_dir}")
        return False
    ok = True
    for line in manifest.read_text().splitlines():
        digest, rel = line.split("  ", 1)
        p = model_dir / rel
        if not p.exists():
            print(f"  MISSING  {rel}")
            ok = False
        elif sha256(p) != digest:
            print(f"  CHANGED  {rel}")
            ok = False
    print("Manifest OK — every file matches." if ok else "Manifest FAILED.")
    return ok


def offline_load_test(model_dir: Path) -> None:
    # Same switches the real embedder sets. If anything below tries the
    # network, it errors instead of silently downloading.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(str(model_dir), device="cpu")
    v = m.encode(["offline load test"], normalize_embeddings=True)
    print(f"Offline load OK — {v.shape[1]}-dimensional vectors.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="only check the manifest + offline load")
    args = ap.parse_args()
    model_dir = settings.embed_model_dir

    if not args.verify:
        from huggingface_hub import snapshot_download
        print(f"Downloading {settings.embed_model_id} -> {model_dir}")
        snapshot_download(repo_id=settings.embed_model_id, local_dir=str(model_dir),
                          allow_patterns=ALLOW)
        write_manifest(model_dir)

    if verify(model_dir):
        offline_load_test(model_dir)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
