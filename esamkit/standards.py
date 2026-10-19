"""
The standards library: real clause text ESAM can cite, look up and check.

    data/standards/library.yaml       catalogue: each standard's source, date,
                                       distribution, SHA-256 of the source PDF,
                                       and the tables encoded as data
    data/standards/<ID>.md            the clause text, one '## <clause> <title>'
                                       heading per clause

Only public US Government documents are held as text. A copyrighted
standard (ISO/IEC/IEEE 29148) is listed by name with the terms ESAM uses
from it, never its text.

A citation is '<standard> <clause>', e.g. 'MIL-STD-882E 4.4.2',
'MIL-STD-882E B.2.2.5a' (item a of B.2.2.5), 'MIL-STD-882E Table V',
'DI-IPSC-81433A 3.7'. `resolve` says whether a citation exists; it never
guesses a near miss.

    python -m esamkit.standards                       # the catalogue
    python -m esamkit.standards show "MIL-STD-882E 4.4.2"
    python -m esamkit.standards search "safety critical"
    python -m esamkit.standards extract-882e docs/MIL-STD-882E.pdf   # rebuild the .md from the PDF
"""

import argparse
import hashlib
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import yaml

from .config import PROJECT_ROOT

LIBRARY_DIR = PROJECT_ROOT / "data" / "standards"
_CID = r"(?:[A-Z]\.)?\d+(?:\.(?:\d+|x))*"
_HEADING = re.compile(rf"^## (?P<cid>{_CID}) (?P<title>.+)$")
_CITE = re.compile(rf"^(?P<std>[A-Z][A-Z0-9/-]*[A-Z0-9])\s+(?:(?P<table>Table\s+[A-Z0-9-]+)|(?P<cid>{_CID})(?P<item>[a-z])?)$")


class Library:
    def __init__(self, directory: Path = LIBRARY_DIR):
        self.dir = directory
        self.catalogue: dict = yaml.safe_load((directory / "library.yaml").read_text())
        self.standards: dict = self.catalogue["standards"]
        self._clauses: dict[str, dict[str, dict]] = {}

    def clauses(self, std: str) -> dict[str, dict]:
        """{clause id: {title, text}} for a standard held as text ({} if held by name only)."""
        if std not in self._clauses:
            f = self.standards.get(std, {}).get("text")
            out: dict[str, dict] = {}
            if f:
                cur = None
                for line in (self.dir / f).read_text().splitlines():
                    if m := _HEADING.match(line):
                        cur = out.setdefault(m["cid"], {"title": m["title"], "lines": []})
                    elif cur is not None and not line.startswith("```"):
                        cur["lines"].append(line)
                for c in out.values():
                    c["text"] = "\n".join(c.pop("lines")).strip()
            self._clauses[std] = out
        return self._clauses[std]

    def resolve(self, citation: str) -> dict:
        """{'ok': bool, 'standard', 'clause', 'title', 'text' | 'reason'} for one citation."""
        m = _CITE.match(citation.strip())
        if not m:
            return {"ok": False, "citation": citation, "reason": "not of the form '<standard> <clause>' or '<standard> Table <n>'"}
        std = m["std"]
        if std not in self.standards:
            return {"ok": False, "citation": citation, "reason": f"{std} is not in the standards library"}
        spec = self.standards[std]
        if not spec.get("text"):
            return {"ok": False, "citation": citation,
                    "reason": f"{std} is held by name only ({spec.get('holding', 'no text')}); cite it without a clause"}
        cl = self.clauses(std)
        if m["table"]:
            label = m["table"].upper().replace("  ", " ")
            hit = next((cid for cid, c in cl.items() if re.search(rf"\b{re.escape(label)}\.", c["text"])), None)
            if not hit:
                return {"ok": False, "citation": citation, "reason": f"{std} has no {m['table']}"}
            return {"ok": True, "citation": citation, "standard": std, "clause": hit, "title": m["table"],
                    "text": cl[hit]["text"]}
        cid = m["cid"]
        if cid not in cl:
            return {"ok": False, "citation": citation, "reason": f"{std} has no clause {cid} in the library"}
        if m["item"] and not re.search(rf"(^|\n)\s*{m['item']}\.\s+\S", cl[cid]["text"]):
            return {"ok": False, "citation": citation, "reason": f"{std} {cid} has no item {m['item']}."}
        return {"ok": True, "citation": citation, "standard": std, "clause": cid, "title": cl[cid]["title"],
                "text": cl[cid]["text"]}

    def search(self, term: str, limit: int = 8) -> list[dict]:
        """Clauses whose title or text contains every word of `term` (case-insensitive)."""
        words = [w for w in term.lower().split() if w]
        out = []
        for std in self.standards:
            for cid, c in self.clauses(std).items():
                hay = f"{c['title']} {c['text']}".lower()
                if words and all(w in hay for w in words):
                    score = sum(hay.count(w) for w in words) + 5 * sum(w in c["title"].lower() for w in words)
                    out.append({"citation": f"{std} {cid}", "title": c["title"], "score": score,
                                "excerpt": _excerpt(c["text"], words[0])})
        return sorted(out, key=lambda r: -r["score"])[:limit]

    # ── MIL-STD-882E 4.4 as data ──

    def swci(self, severity: str, control_category: int) -> str:
        """Table V: severity category × software control category → SwCI."""
        t = self.catalogue["tables"]["MIL-STD-882E Table V"]
        return t["matrix"][int(control_category)][t["severity_columns"].index(severity)]

    def level_of_rigor(self, swci: str) -> str:
        return self.catalogue["tables"]["MIL-STD-882E Table V"]["level_of_rigor"][swci]

    def unmet_lor_risk(self, swci: str) -> str:
        return self.catalogue["tables"]["MIL-STD-882E Table VI"][swci]


def _excerpt(text: str, word: str, n: int = 220) -> str:
    flat = " ".join(text.split())
    i = max(0, flat.lower().find(word) - 60)
    return ("…" if i else "") + flat[i:i + n] + ("…" if i + n < len(flat) else "")


@lru_cache(maxsize=1)
def default_library() -> Library:
    return Library()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── Building MIL-STD-882E.md from the public PDF ─────────────────────────────

# Parts of the standard held as text: definitions, the general requirements
# (section 4, including Tables I-VI) and Appendix B (software system safety).
_882E_PARTS = [("3.2 Definitions.", "4. GENERAL REQUIREMENTS"),
               ("4.1 General.", "5. DETAILED REQUIREMENTS"),
               ("B.1 Scope.", None)]
_NOISE = re.compile(r"^\s*(Downloaded from https://www\.everyspec\.com|MIL-STD-882E|APPENDIX [A-Z]|\d{1,3})\s*$")
_CLAUSE_START = re.compile(r"^\s*(?P<cid>B\.\d+(?:\.\d+)*|\d+(?:\.\d+)+)\.?\s+(?P<title>[A-Z(][^.]{1,120}?)\.(?:\s+(?P<rest>.*))?$")
_CLAUSE_OPEN = re.compile(r"^\s*(B\.\d+(?:\.\d+)*|\d+(?:\.\d+)+)\.?\s+[A-Z][^.]*$")   # a title that wraps


def extract_882e(pdf: Path) -> str:
    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True).stdout
    lines = txt.splitlines()
    body: list[str] = []
    for start, end in _882E_PARTS:
        i = next(k for k, ln in enumerate(lines) if ln.strip().startswith(start) and "...." not in ln)
        j = next((k for k in range(i + 1, len(lines)) if end and lines[k].strip().startswith(end)), len(lines))
        body += [ln for ln in lines[i:j] if not _NOISE.match(ln)]
    out, cur, k = [], None, 0
    while k < len(body):
        ln = body[k]
        k += 1
        if _CLAUSE_OPEN.match(ln) and k < len(body) and "." in body[k]:
            joined = ln.rstrip() + " " + body[k].strip()
            if _CLAUSE_START.match(joined):
                ln, k = joined, k + 1
        m = _CLAUSE_START.match(ln)
        if m:
            if cur:
                out.append(cur)
            cur = {"cid": m["cid"], "title": m["title"].strip(), "lines": [m["rest"] or ""]}
        elif cur:
            cur["lines"].append(ln)
    out.append(cur)
    parts = []
    for c in out:
        text = "\n".join(c["lines"]).strip("\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        parts.append(f"## {c['cid']} {c['title']}\n\n```text\n{_dedent(text)}\n```\n")
    return "\n".join(parts)


def _dedent(text: str) -> str:
    lines = text.splitlines()
    pad = min((len(ln) - len(ln.lstrip()) for ln in lines[1:] if ln.strip()), default=0)
    return "\n".join([lines[0].strip()] + [ln[pad:] for ln in lines[1:]]) if lines else ""


def main() -> None:
    ap = argparse.ArgumentParser(description="ESAM standards library")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("citation")
    q = sub.add_parser("search"); q.add_argument("term")
    x = sub.add_parser("extract-882e"); x.add_argument("pdf", type=Path)
    args = ap.parse_args()
    lib = default_library()
    if args.cmd == "show":
        r = lib.resolve(args.citation)
        print(f"{r['standard']} {r['clause']} {r['title']}\n\n{r['text']}" if r["ok"] else f"✗ {r['reason']}")
        sys.exit(0 if r["ok"] else 1)
    if args.cmd == "search":
        for r in lib.search(args.term):
            print(f"{r['citation']:<28} {r['title']}\n    {r['excerpt']}")
        return
    if args.cmd == "extract-882e":
        want = lib.standards["MIL-STD-882E"]["source"]["sha256"]
        got = sha256(args.pdf)
        if got != want:
            sys.exit(f"{args.pdf} has SHA-256 {got}; the library was built from {want}")
        (LIBRARY_DIR / lib.standards["MIL-STD-882E"]["text"]).write_text(
            "# MIL-STD-882E — System Safety (extract)\n\n" + _882E_HEADER + extract_882e(args.pdf))
        print(f"wrote {LIBRARY_DIR / lib.standards['MIL-STD-882E']['text']}")
        return
    for sid, s in lib.standards.items():
        held = f"{len(lib.clauses(sid))} clauses" if s.get("text") else s.get("holding", "by name only")
        print(f"{sid:<22} {s['title'][:52]:<52} {str(s.get('date', '')):<11} {held}")


_882E_HEADER = """> Source: MIL-STD-882E, 11 May 2012, Department of Defense Standard Practice, System Safety.
> A public US Government work (freely available; this copy's cover carries no distribution statement).
> Extracted by `python -m esamkit.standards extract-882e` from the public PDF (SHA-256 in
> library.yaml): section 3.2 (definitions), section 4 (general requirements, Tables I–VI) and
> Appendix B (software system safety). Layout text as extracted; tables keep their columns.
> Not included: section 5 tasks, Appendix A, and Change 1 (27 Sep 2023).

"""


if __name__ == "__main__":
    main()
