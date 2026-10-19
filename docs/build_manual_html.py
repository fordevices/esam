"""Build ESAM_SE_User_Manual.html from ESAM_SE_User_Manual.md.

The Markdown stays the source of truth: edit it, then rerun

    .venv/bin/python docs/build_manual_html.py

The output is one self-contained HTML file (no CDN, no web fonts, no network)
so it opens on the air-gapped side too. Interactive figures are attached to
section numbers in FIGURES below; a figure whose section disappears is skipped
with a warning rather than failing the build.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

HERE = Path(__file__).resolve().parent
SRC = HERE / "ESAM_SE_User_Manual.md"
OUT = HERE / "ESAM_SE_User_Manual.html"


# --------------------------------------------------------------------------- #
# Markdown -> HTML fragments
# --------------------------------------------------------------------------- #
def slugify(text: str, seen: dict[str, int]) -> str:
    """GitHub-style anchor, so the manual's existing #links keep working."""
    s = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
    n = seen.get(s, 0)
    seen[s] = n + 1
    return s if n == 0 else f"{s}-{n}"


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def split_front(md_text: str) -> tuple[str, list[tuple[str, str]], str]:
    """Title, the bold-label lines under it, and the body after the first ---."""
    head, _, body = md_text.partition("\n---\n")
    title = re.search(r"^# (.+)$", head, re.M).group(1).strip()
    meta = re.findall(r"^\*\*(.+?):\*\*\s*(.+)$", head, re.M)
    return title, meta, body


def render_body(body: str):
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    body = re.sub(r"^---\s*$", "", body, flags=re.M)  # chapter rules become section breaks
    tokens = md.parse(body)
    seen: dict[str, int] = {}
    heads = []  # (level, number, title_text, id)
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and tok.tag in ("h2", "h3"):
            text = "".join(c.content for c in tokens[i + 1].children if c.type in ("text", "code_inline"))
            hid = slugify(text, seen)
            tok.attrSet("id", hid)
            m = re.match(r"(\d+(?:\.\d+)?)\.?\s+(.*)", text)
            num, title = (m.group(1), m.group(2)) if m else ("", text)
            heads.append((int(tok.tag[1]), num, title, hid))
    return md.renderer.render(tokens, md.options, {}), heads


# --------------------------------------------------------------------------- #
# Post-processing
# --------------------------------------------------------------------------- #
CHIP_WORDS = {
    "Reported": "info", "Blocks approval": "crit",
    "queued": "none", "running": "info", "done": "ok", "failed": "crit",
}
TRUST = {"High": "ok", "Medium": "part", "Low until reviewed": "crit"}


def label_tables(frag: str) -> str:
    """Wrap tables, add data-label per cell (for the narrow-screen card layout), add chips."""

    def one(m: re.Match) -> str:
        t = m.group(0)
        heads = [strip_tags(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)]

        def row(rm: re.Match) -> str:
            cells = iter(heads)
            return re.sub(r"<td([^>]*)>", lambda cm: f'<td{cm.group(1)} data-label="{html.escape(next(cells, ""))}">', rm.group(0))

        t = re.sub(r"<tr>.*?</tr>", row, t, flags=re.S)

        def chip(cm: re.Match) -> str:
            attrs, inner = cm.group(1), cm.group(2)
            plain = strip_tags(inner)
            if plain in CHIP_WORDS:
                return f'<td{attrs}><span class="chip-s {CHIP_WORDS[plain]}">{plain}</span></td>'
            tm = re.match(r"(High|Medium|Low until reviewed):\s*(.*)", inner, re.S)
            if tm:
                return f'<td{attrs}><span class="chip-s {TRUST[tm.group(1)]}">{tm.group(1)}</span> {tm.group(2)}</td>'
            return cm.group(0)

        t = re.sub(r"<td([^>]*)>(.*?)</td>", chip, t, flags=re.S)
        ncols = len(heads)
        empty_head = all(not h for h in heads[:1])
        cls = f"cols-{ncols}" + (" lead-empty" if empty_head else "")
        return f'<div class="tbl {cls}">{t}</div>'

    return re.sub(r"<table>.*?</table>", one, frag, flags=re.S)


def decorate(frag: str, xref: dict[str, tuple[str, str]]) -> str:
    # Code blocks: language tag + copy button.
    def code(m: re.Match) -> str:
        lang = m.group(1) or "text"
        return (f'<div class="code"><div class="code-bar"><span>{lang}</span>'
                f'<button class="copy" type="button">Copy</button></div>'
                f'<pre><code>{m.group(2)}</code></pre></div>')

    frag = re.sub(r'<pre><code(?: class="language-(\w+)")?>(.*?)</code></pre>', code, frag, flags=re.S)

    # Blockquotes become callouts; the one-line summary gets its own style.
    def quote(m: re.Match) -> str:
        inner = m.group(1)
        kind = "tldr" if "One-line version" in inner else "quote"
        inner = re.sub(r"(?<!>)\n(?!<)", "<br>", inner)
        return f'<blockquote class="callout {kind}">{inner}</blockquote>'

    frag = re.sub(r"<blockquote>(.*?)</blockquote>", quote, frag, flags=re.S)

    # "§4.3" -> link to that section, except where § belongs to another document
    # ("ICD-BRK-001 §2", "ESAM Pilot Implementation §4.3").
    def sect(m: re.Match) -> str:
        num = m.group(1)
        if num not in xref:
            return m.group(0)
        hid, title = xref[num]
        return f'<a class="xref" href="#{hid}" data-tip="§{num} {html.escape(title, quote=True)}">§{num}</a>'

    parts = re.split(r"(<pre>.*?</pre>|<code>.*?</code>|<h[23][^>]*>.*?</h[23]>)", frag, flags=re.S)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"(?<![A-Z0-9]\d )(?<!Implementation )§(\d+(?:\.\d+)?)", sect, parts[i])
    return "".join(parts)


def read_minutes(frag: str) -> int:
    return max(1, round(len(strip_tags(frag).split()) / 220))


# --------------------------------------------------------------------------- #
# Interactive figures, keyed by the section number they close
# --------------------------------------------------------------------------- #
def fig(fid: str, title: str, body: str, caption: str = "") -> str:
    cap = f'<figcaption>{caption}</figcaption>' if caption else ""
    return (f'<figure class="fig" id="{fid}"><div class="fig-head"><span class="fig-kicker">Interactive</span>'
            f'<span class="fig-title">{title}</span></div><div class="fig-body">{body}</div>{cap}</figure>')


STD_TOGGLE = ('<button class="std-toggle" type="button" aria-pressed="false">'
              '<span class="sw" aria-hidden="true"></span>STD-BRK-001 loaded</button>')

FIGURES = {
    "1.3": fig("fig-workflow", "From contract WBS to a locked baseline",
               '<ol class="flow" data-flow></ol><div class="fig-ctl"><button class="btn" data-play>▶ Play the flow</button>'
               '<span class="legend"><i class="who se"></i>SE <i class="who auto"></i>Automatic <i class="who it"></i>IT/SW</span></div>',
               "Two gates protect the baseline: the schema check (step 3) and your approval (step 6)."),
    "2.2": fig("fig-rows", "Five documents, 28 rows",
               '<div class="bars" data-rows></div><div class="cards-out" data-cards aria-live="polite"></div>',
               "Select a document to see its chunks: one index card per row, numbered so they can be put back in order."),
    "2.3": fig("fig-chunk", "What ESAM does to a large Word document",
               '<ol class="chev" data-chev></ol><div class="detail" data-chev-detail></div>',
               "Select a step to see why it matters."),
    "3": fig("fig-vectors", "A map of meaning",
             '<div class="vec"><div class="vec-plot" data-vplot></div><div class="vec-side">'
             '<div class="ctl-label">Ask a question</div><div class="chips" data-vq></div>'
             '<div class="ctl-label" style="margin-top:14px">Closest chunks</div><ol class="hits" data-vhits></ol></div></div>',
             "Illustrative only: a 2-D sketch of what are really 768 dimensions. Positions and scores are made up for the picture; the chunk labels are real sections of the SYNTH-LTV documents."),
    "4.4": fig("fig-graph", "Walk the SYNTH-LTV baseline",
               '<div class="fig-ctl wrap"><button class="btn" data-walk="up">↑ Where does ICD-BRK-001 sit?</button>'
               '<button class="btn" data-walk="down">↓ What\'s under Suspension/Steering?</button>'
               '<button class="btn" data-walk="across">↔ If the Brake ECU changes EBC1, who\'s affected?</button></div>'
               '<div class="graph-wrap"><div class="graph" data-graph></div></div>'
               '<div class="walk-out"><div class="walk-info" data-winfo>Choose a question, or select any box.</div>'
               '<div class="tbl cols-4"><table class="walk-tbl"><thead><tr><th>depth</th><th>id</th><th>name</th><th>link followed</th></tr></thead><tbody data-wrows><tr class="ph"><td colspan="4" data-label="">Rows appear here as the walk proceeds.</td></tr></tbody></table></div></div>',
               "Start, step, repeat: each step adds one row, and the walk stops when there's nowhere new to go. Dashed orange = candidate interface."),
    "5.2": fig("fig-coverage", "Required documents and roll-up",
               '<div class="fig-ctl wrap"><div class="seg" role="group" aria-label="Rule set">'
               '<button data-mode="rules" aria-pressed="true">Ontology rules</button>'
               '<button data-mode="fixed" aria-pressed="false">Fixed checklist</button></div>' + STD_TOGGLE + '</div>'
               '<div class="tiles" data-ctiles></div><div class="tbl cov"><table data-cmatrix></table></div>'
               '<div class="ctl-label" style="margin-top:18px">Roll-up, walking up the tree</div><div class="rollup" data-rollup></div>',
               "✓ present · ✗ missing · – not required · ⚠ safety-critical. Switch to the fixed checklist to see why rules beat one list for every subsystem; load the STD to watch the Brake ECU gap close."),
    "7.2": fig("fig-gap", "How ESAM answers “How is SRS-BRK-011 verified?”",
               '<div class="fig-ctl">' + STD_TOGGLE + '</div><ol class="decide" data-decide></ol>',
               "The gap is detected from the baseline, not guessed by the AI."),
    "8.4": '<p class="seealso">↗ Try these rules interactively in the <a href="#fig-coverage">coverage figure</a> (§5.2).</p>',
    "10.3": fig("fig-trace", "Requirement coverage, SYNTH-LTV",
                '<div class="fig-ctl">' + STD_TOGGLE + '<span class="big" data-tsum></span></div>'
                '<div data-tgrid></div><div class="detail" data-tdetail>Select a requirement.</div>',
                "Green: a test case's <code>Verifies:</code> line names it. Red outline: nothing verifies it yet."),
    "11.1": fig("fig-swci", "MIL-STD-882E Table V: from your assessment to a SwCI",
                '<div class="swci"><div class="tbl cols-5 keep"><table data-swci></table></div><div class="swci-out" data-swci-out></div></div>',
                "You and the system safety engineer choose the severity and control category; ESAM only looks the answer up. Text from ESAM's standards library."),
    "12.4": fig("fig-review", "A document review, end to end",
                '<ol class="pipe" data-pipe></ol><div class="detail" data-pipe-detail></div>',
                "The agents stop at the approval request. Nothing enters the baseline until you approve."),
    "12.9": fig("fig-cost", "Time and tokens per task",
                '<div class="fig-ctl"><div class="seg" role="group" aria-label="Measure">'
                '<button data-cm="tokens" aria-pressed="true">Model tokens</button>'
                '<button data-cm="time" aria-pressed="false">Typical time</button></div></div><div class="bars" data-cost></div>',
                "Measured on the learning system (GPT-5.1)."),
}


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build() -> str:
    title, meta, body = split_front(SRC.read_text(encoding="utf-8"))
    frag, heads = render_body(body)
    xref = {num: (hid, title_) for _, num, title_, hid in heads if num}

    # Split into chapters at <h2>, subsections at <h3>.
    chunks = re.split(r'(?=<h2 id=")', frag)
    chapters = []
    used = set()
    for ch in chunks:
        if not ch.startswith("<h2"):
            continue
        hm = re.match(r'<h2 id="([^"]+)">(.*?)</h2>', ch, re.S)
        hid, htxt = hm.group(1), strip_tags(hm.group(2))
        num, ctitle = re.match(r"(\d+)\.?\s+(.*)", htxt).groups()
        rest = ch[hm.end():]
        subs = re.split(r'(?=<h3 id=")', rest)
        out_subs = []
        has_fig = False
        for j, s in enumerate(subs):
            if s.startswith("<h3"):
                sm = re.match(r'<h3 id="([^"]+)">(.*?)</h3>', s, re.S)
                snum = re.match(r"(\d+\.\d+)", strip_tags(sm.group(2)))
                key = snum.group(1) if snum else ""
                stitle = strip_tags(sm.group(2))
                anchor = (f'<h3 id="{sm.group(1)}">{sm.group(2)}'
                          f'<a class="hlink" href="#{sm.group(1)}" aria-label="Link to this section">#</a></h3>')
                s = anchor + s[sm.end():]
                cls = "sub status" if stitle.endswith("Tooling status") else "sub"
            else:
                key, cls = num, "sub intro"
                if not s.strip():
                    if num in FIGURES:
                        s = ""
                    else:
                        continue
            if key in FIGURES:
                s += FIGURES[key]
                used.add(key)
                has_fig = has_fig or 'class="fig"' in FIGURES[key]
            out_subs.append(f'<div class="{cls}">{s}</div>')
        inner = decorate(label_tables("".join(out_subs)), xref)
        chapters.append(dict(num=num, title=ctitle, id=hid, html=inner,
                             nsubs=sum(1 for h in heads if h[0] == 3 and h[1].startswith(num + ".")),
                             mins=read_minutes(inner), fig=has_fig))

    for key in FIGURES:
        if key not in used:
            print(f"warning: no section {key}; figure skipped", file=sys.stderr)

    # Sidebar + hero cards + chapters.
    toc, cards, sections = [], [], []
    for i, c in enumerate(chapters):
        hue = f"var(--hue-{i % 6 + 1})"
        subs = "".join(f'<li><a href="#{hid}">{n} {html.escape(t)}</a></li>'
                       for lvl, n, t, hid in heads if lvl == 3 and n.startswith(c["num"] + "."))
        toc.append(f'<li style="--c:{hue}"><a class="toc-ch" href="#{c["id"]}"><span class="n">{c["num"]}</span>'
                   f'<span>{html.escape(c["title"])}</span></a>{f"<ol>{subs}</ol>" if subs else ""}</li>')
        badge = '<span class="tag">Interactive</span>' if c["fig"] else ""
        cards.append(f'<a class="card" href="#{c["id"]}" style="--c:{hue}"><span class="card-n">{c["num"]}</span>'
                     f'<span class="card-t">{html.escape(c["title"])}</span>'
                     f'<span class="card-m">{c["mins"]} min read{" · " + str(c["nsubs"]) + " parts" if c["nsubs"] else ""}{badge}</span></a>')
        prev_l = (f'<a class="pn prev" href="#{chapters[i-1]["id"]}"><small>← Previous</small>{chapters[i-1]["num"]}. {html.escape(chapters[i-1]["title"])}</a>'
                  if i else '<span></span>')
        next_l = (f'<a class="pn next" href="#{chapters[i+1]["id"]}"><small>Next →</small>{chapters[i+1]["num"]}. {html.escape(chapters[i+1]["title"])}</a>'
                  if i + 1 < len(chapters) else '<a class="pn next" href="#top"><small>Done</small>Back to the top ↑</a>')
        sections.append(
            f'<section class="chap" style="--c:{hue}" data-ch="{c["num"]}" data-title="{html.escape(c["title"], quote=True)}">'
            f'<header class="chap-head"><span class="chap-n">{c["num"]}</span><div>'
            f'<h2 id="{c["id"]}">{html.escape(c["title"])}<a class="hlink" href="#{c["id"]}" aria-label="Link to this section">#</a></h2>'
            f'<div class="chap-meta">{c["mins"]} min read{" · " + str(c["nsubs"]) + " parts" if c["nsubs"] else ""}{badge}</div></div></header>'
            f'{c["html"]}<nav class="prevnext">{prev_l}{next_l}</nav></section>')

    meta_html = "".join(f'<div class="meta-card"><div class="meta-k">{html.escape(k)}</div>'
                        f'<div class="meta-v">{MarkdownIt().renderInline(v)}</div></div>' for k, v in meta)

    page = TEMPLATE
    for k, v in {
        "%%TITLE%%": html.escape(title),
        "%%META%%": meta_html,
        "%%CARDS%%": "".join(cards),
        "%%TOC%%": "".join(toc),
        "%%BODY%%": "".join(sections),
        "%%CSS%%": CSS,
        "%%JS%%": JS,
    }.items():
        page = page.replace(k, v)
    return page


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ESAM SE User Manual</title>
<!--
  GENERATED from ESAM_SE_User_Manual.md by docs/build_manual_html.py.
  Don't edit this file: edit the Markdown and rerun the script.
  Self-contained: no CDN, no web fonts, no network, so it opens on the
  air-gapped side too. Colours follow the SE Cockpit / how_esam_finds_answers.html.
-->
<style>%%CSS%%</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="topbar">
  <button class="iconbtn menu" type="button" aria-label="Open contents" aria-expanded="false" aria-controls="side">☰</button>
  <a class="brand" href="#top"><span class="logo" aria-hidden="true">E</span><span>ESAM <b>SE User Manual</b></span></a>
  <span class="crumb" aria-live="polite"></span>
  <span class="spacer"></span>
  <button class="iconbtn searchbtn" type="button" aria-label="Search (press /)" title="Search  /">⌕</button>
  <button class="iconbtn theme" type="button" aria-label="Colour theme" title="Theme: auto"></button>
  <div class="progress" aria-hidden="true"><i></i></div>
</div>
<div class="layout">
  <aside id="side" class="side" aria-label="Contents">
    <div class="search">
      <input id="q" type="search" placeholder="Search the manual" autocomplete="off" aria-label="Search the manual">
      <kbd>/</kbd>
      <div class="search-nav" hidden><span class="count"></span>
        <button type="button" data-hit="-1" aria-label="Previous match">↑</button>
        <button type="button" data-hit="1" aria-label="Next match">↓</button></div>
    </div>
    <nav class="toc"><ol>%%TOC%%</ol></nav>
  </aside>
  <div class="scrim" hidden></div>
  <main id="main">
    <header class="hero" id="top">
      <div class="hero-inner">
        <div class="eyebrow">ESAM · Systems Engineering</div>
        <h1>%%TITLE%%</h1>
        <p class="lede">The baseline, the documents ESAM reads, and the agents that read them, explained in SE terms. Twelve topics, with interactive figures you can poke at.</p>
        <div class="meta">%%META%%</div>
      </div>
    </header>
    <div class="content">
      <h2 class="cards-h">Contents</h2>
      <div class="cards">%%CARDS%%</div>
      %%BODY%%
      <footer class="foot">Generated from <code>ESAM_SE_User_Manual.md</code>. Living document: edit the Markdown, then run <code>docs/build_manual_html.py</code>.</footer>
    </div>
  </main>
</div>
<button class="totop" type="button" aria-label="Back to top">↑</button>
<script>%%JS%%</script>
</body>
</html>
"""

CSS = r"""
:root {
  --paper: #f3f5f8; --sheet: #ffffff; --sheet-2: #f8fafc; --ink: #142030; --ink-2: #4f5d6c; --rule: #dbe1e8;
  --accent: #1f5f8b; --signal: #b5630a;
  --ok: #2e7a4c; --ok-bg: #e4f2e9; --part: #8a6a00; --part-bg: #fbf3d9;
  --crit: #b3261e; --crit-bg: #fbe9e7; --none: #5d6b78; --none-bg: #eef1f4; --info: #1f5f8b; --info-bg: #e5eff7;
  --t-top: #5d6b78; --t-sys: #2b5d87; --t-sub: #0d7584; --t-doc: #7b4f8f;
  --hue-1: #1f5f8b; --hue-2: #0d7584; --hue-3: #7b4f8f; --hue-4: #2e7a4c; --hue-5: #b5630a; --hue-6: #4a5bb0;
  --shadow: 0 1px 2px rgb(16 32 48 / .05), 0 6px 22px rgb(16 32 48 / .07);
  --glass: rgb(255 255 255 / .78);
  --f-display: "Bahnschrift", "DIN Alternate", "Roboto Condensed", "Arial Narrow", system-ui, sans-serif;
  --f-body: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif;
  --f-mono: ui-monospace, "SF Mono", "Cascadia Mono", "DejaVu Sans Mono", Menlo, monospace;
  --top: 56px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #0d1217; --sheet: #151d25; --sheet-2: #19232c; --ink: #e2e8ee; --ink-2: #9aa8b5; --rule: #2a3642;
  --accent: #6aa8d8; --signal: #eb9a3c;
  --ok: #6cc28f; --ok-bg: #15301f; --part: #e3c14f; --part-bg: #33290c;
  --crit: #ff7b6f; --crit-bg: #3a1d1a; --none: #a3b0bc; --none-bg: #222c35; --info: #6aa8d8; --info-bg: #182c3d;
  --t-top: #96a5b3; --t-sys: #72a4d0; --t-sub: #4fb7c5; --t-doc: #c59bd8;
  --hue-1: #6aa8d8; --hue-2: #4fb7c5; --hue-3: #c59bd8; --hue-4: #6cc28f; --hue-5: #eb9a3c; --hue-6: #8f9ef0;
  --shadow: 0 1px 2px rgb(0 0 0 / .3), 0 8px 26px rgb(0 0 0 / .25);
  --glass: rgb(21 29 37 / .8);
  color-scheme: dark;
} }
:root[data-theme="dark"] {
  --paper: #0d1217; --sheet: #151d25; --sheet-2: #19232c; --ink: #e2e8ee; --ink-2: #9aa8b5; --rule: #2a3642;
  --accent: #6aa8d8; --signal: #eb9a3c;
  --ok: #6cc28f; --ok-bg: #15301f; --part: #e3c14f; --part-bg: #33290c;
  --crit: #ff7b6f; --crit-bg: #3a1d1a; --none: #a3b0bc; --none-bg: #222c35; --info: #6aa8d8; --info-bg: #182c3d;
  --t-top: #96a5b3; --t-sys: #72a4d0; --t-sub: #4fb7c5; --t-doc: #c59bd8;
  --hue-1: #6aa8d8; --hue-2: #4fb7c5; --hue-3: #c59bd8; --hue-4: #6cc28f; --hue-5: #eb9a3c; --hue-6: #8f9ef0;
  --shadow: 0 1px 2px rgb(0 0 0 / .3), 0 8px 26px rgb(0 0 0 / .25);
  --glass: rgb(21 29 37 / .8);
  color-scheme: dark;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: calc(var(--top) + 16px); }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } * { transition: none !important; animation: none !important; } }
html, body { margin: 0; }
body { background: var(--paper); color: var(--ink); font: 16px/1.62 var(--f-body); -webkit-font-smoothing: antialiased; }
a { color: var(--accent); text-underline-offset: 2px; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
button { font: inherit; color: inherit; }
code { font-family: var(--f-mono); font-size: .86em; background: var(--none-bg); border-radius: 4px; padding: .08em .35em; overflow-wrap: anywhere; }
.skip { position: absolute; left: -999px; } .skip:focus { left: 12px; top: 12px; z-index: 99; background: var(--sheet); padding: 8px 12px; }

/* ---------- top bar ---------- */
.topbar { position: sticky; top: 0; z-index: 40; height: var(--top); display: flex; align-items: center; gap: 10px;
  padding: 0 max(16px, env(safe-area-inset-right)) 0 max(12px, env(safe-area-inset-left));
  background: var(--glass); backdrop-filter: saturate(1.6) blur(14px); -webkit-backdrop-filter: saturate(1.6) blur(14px);
  border-bottom: 1px solid var(--rule); }
.brand { display: flex; align-items: center; gap: 10px; color: var(--ink); text-decoration: none; font: 15px var(--f-display); white-space: nowrap; }
.brand b { font-weight: 600; }
.logo { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; color: #fff; font: 700 16px var(--f-display);
  background: linear-gradient(135deg, #1f5f8b, #0d7584); box-shadow: 0 2px 8px rgb(13 117 132 / .35); }
.crumb { color: var(--ink-2); font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.crumb:not(:empty)::before { content: "/"; margin: 0 8px 0 2px; opacity: .5; }
.spacer { flex: 1; }
.iconbtn { width: 38px; height: 38px; border-radius: 10px; border: 1px solid transparent; background: none; cursor: pointer; font-size: 18px; display: grid; place-items: center; flex: none; }
.iconbtn:hover { background: var(--none-bg); border-color: var(--rule); }
.menu, .searchbtn { display: none; }
.progress { position: absolute; left: 0; right: 0; bottom: -1px; height: 3px; }
.progress i { display: block; height: 100%; width: 0; background: linear-gradient(90deg, var(--accent), var(--hue-2), var(--signal)); }

/* ---------- layout ---------- */
.layout { display: grid; grid-template-columns: 300px minmax(0, 1fr); }
.side { position: sticky; top: var(--top); height: calc(100vh - var(--top)); overflow-y: auto; border-right: 1px solid var(--rule);
  background: var(--sheet); padding: 16px 12px 40px; overscroll-behavior: contain; }
.scrim { display: none; }
@media (max-width: 1080px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
  .menu, .searchbtn { display: grid; }
  .side { position: fixed; z-index: 60; top: 0; left: 0; bottom: 0; height: 100vh; width: min(340px, 88vw);
    transform: translateX(-102%); transition: transform .25s ease; box-shadow: var(--shadow); }
  body.nav-open .side { transform: none; }
  body.nav-open .scrim { display: block; position: fixed; inset: 0; z-index: 50; background: rgb(0 0 0 / .35); }
}
@media (max-width: 560px) { .brand > span:last-child { display: none; } }

/* search */
.search { position: relative; margin: 0 4px 14px; }
.search input { width: 100%; padding: 9px 36px 9px 12px; border-radius: 10px; border: 1px solid var(--rule); background: var(--paper); color: var(--ink); font: inherit; font-size: 14.5px; }
.search input:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.search kbd { position: absolute; right: 10px; top: 9px; font: 12px var(--f-mono); color: var(--ink-2); border: 1px solid var(--rule); border-radius: 4px; padding: 0 5px; }
.search-nav[hidden] { display: none; }
.search-nav { display: flex; align-items: center; gap: 4px; margin-top: 6px; font-size: 13px; color: var(--ink-2); }
.search-nav .count { flex: 1; }
.search-nav button { border: 1px solid var(--rule); background: var(--sheet); border-radius: 6px; width: 28px; height: 26px; cursor: pointer; }
mark.hit { background: color-mix(in srgb, var(--signal) 28%, transparent); color: inherit; border-radius: 2px; }
mark.hit.cur { background: var(--signal); color: #fff; }

/* toc */
.toc ol { list-style: none; margin: 0; padding: 0; }
.toc > ol > li { margin-bottom: 2px; }
.toc-ch { display: flex; gap: 10px; align-items: baseline; padding: 7px 10px; border-radius: 9px; color: var(--ink); text-decoration: none; font-size: 14.5px; line-height: 1.35; }
.toc-ch .n { flex: none; width: 24px; height: 24px; border-radius: 7px; display: inline-grid; place-items: center; font: 600 12px var(--f-mono);
  color: var(--c); background: color-mix(in srgb, var(--c) 13%, transparent); transform: translateY(5px); }
.toc-ch:hover { background: var(--none-bg); }
.toc li.active > .toc-ch { background: color-mix(in srgb, var(--c) 11%, transparent); font-weight: 600; }
.toc li.active > .toc-ch .n { background: var(--c); color: var(--sheet); }
.toc li ol { display: none; margin: 2px 0 8px 22px; border-left: 2px solid color-mix(in srgb, var(--c) 30%, transparent); }
.toc li.active ol { display: block; }
.toc li ol a { display: block; padding: 4px 10px 4px 14px; font-size: 13.5px; color: var(--ink-2); text-decoration: none; line-height: 1.35; margin-left: -2px; border-left: 2px solid transparent; }
.toc li ol a:hover { color: var(--ink); }
.toc li ol a.active { color: var(--c); border-left-color: var(--c); font-weight: 600; }

/* ---------- hero ---------- */
.hero { position: relative; overflow: hidden; color: #eaf2f8;
  background: radial-gradient(1200px 400px at 85% -10%, rgb(235 154 60 / .28), transparent 60%),
              radial-gradient(900px 500px at 0% 120%, rgb(79 183 197 / .35), transparent 60%),
              linear-gradient(135deg, #0c2236 0%, #123a57 45%, #0d5560 100%); }
.hero::before { content: ""; position: absolute; inset: 0; opacity: .14;
  background-image: linear-gradient(rgb(255 255 255 / .5) 1px, transparent 1px), linear-gradient(90deg, rgb(255 255 255 / .5) 1px, transparent 1px);
  background-size: 34px 34px; mask-image: linear-gradient(180deg, #000 0%, transparent 85%); -webkit-mask-image: linear-gradient(180deg, #000 0%, transparent 85%); }
.hero-inner { position: relative; max-width: 940px; margin: 0 auto; padding: 56px 16px 40px; }
.eyebrow { font: 600 12.5px var(--f-display); letter-spacing: .16em; text-transform: uppercase; color: #9fd3dc; }
.hero h1 { font: 600 clamp(30px, 5vw, 46px)/1.08 var(--f-display); margin: 10px 0 12px; letter-spacing: -.01em; text-wrap: balance; color: #fff; }
.lede { font-size: 18px; color: #c9dbe8; max-width: 62ch; margin: 0 0 26px; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.meta-card { background: rgb(255 255 255 / .08); border: 1px solid rgb(255 255 255 / .16); border-radius: 12px; padding: 12px 14px;
  backdrop-filter: blur(6px); font-size: 14px; color: #dbe7f0; }
.meta-k { font: 600 11.5px var(--f-display); letter-spacing: .12em; text-transform: uppercase; color: #9fd3dc; margin-bottom: 4px; }
.meta-card strong { color: #fff; }
.meta-card code { background: rgb(255 255 255 / .12); color: #fff; }

/* ---------- content ---------- */
.content { max-width: 940px; margin: 0 auto; padding: 8px 16px 80px; }
.cards-h { font: 600 13px var(--f-display); letter-spacing: .14em; text-transform: uppercase; color: var(--ink-2); margin: 34px 0 12px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.card { position: relative; display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; padding: 14px 16px; border-radius: 12px; text-decoration: none; color: var(--ink);
  background: var(--sheet); border: 1px solid var(--rule); box-shadow: var(--shadow); transition: transform .15s, border-color .15s; overflow: hidden; }
.card::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: var(--c); }
.card:hover { transform: translateY(-2px); border-color: var(--c); }
.card-n { grid-row: span 2; font: 600 26px/1 var(--f-display); color: var(--c); min-width: 30px; }
.card-t { font-weight: 600; line-height: 1.3; font-size: 15px; }
.card-m { font-size: 12.5px; color: var(--ink-2); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tag { font: 600 10.5px var(--f-display); letter-spacing: .08em; text-transform: uppercase; color: var(--c, var(--accent)); border: 1px solid currentColor; border-radius: 999px; padding: 0 7px; }

.chap { margin-top: 56px; padding-top: 4px; }
.chap-head { display: flex; gap: 16px; align-items: center; padding: 18px 20px; border-radius: 14px; margin-bottom: 8px;
  background: linear-gradient(120deg, color-mix(in srgb, var(--c) 14%, var(--sheet)), var(--sheet) 70%); border: 1px solid color-mix(in srgb, var(--c) 30%, var(--rule)); }
.chap-n { flex: none; width: 54px; height: 54px; border-radius: 14px; display: grid; place-items: center; font: 600 26px var(--f-display); color: #fff;
  background: linear-gradient(135deg, var(--c), color-mix(in srgb, var(--c) 60%, #000)); box-shadow: 0 4px 14px color-mix(in srgb, var(--c) 35%, transparent); }
.chap h2 { font: 600 clamp(22px, 3.2vw, 28px)/1.2 var(--f-display); margin: 0; text-wrap: balance; }
.chap-meta { font-size: 13px; color: var(--ink-2); margin-top: 4px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sub { padding-top: 6px; }
.chap h3 { font: 600 19px/1.3 var(--f-display); margin: 30px 0 8px; letter-spacing: .005em; }
.hlink { margin-left: 8px; color: var(--ink-2); text-decoration: none; opacity: 0; font-weight: 400; transition: opacity .15s; }
h2:hover .hlink, h3:hover .hlink, .hlink:focus { opacity: .7; }
.hlink.copied::after { content: " link copied"; font-size: 12px; }
.chap p, .chap li { max-width: 75ch; }
.chap ul, .chap ol:not([class]) { padding-left: 22px; }
.chap li { margin: 3px 0; }
.chap li::marker { color: var(--c); }
.chap strong { font-weight: 650; }
.sub.status { margin-top: 24px; padding: 2px 18px 6px; border-radius: 12px; background: var(--part-bg); border: 1px dashed color-mix(in srgb, var(--part) 50%, transparent); }
.sub.status h3 { margin-top: 14px; color: var(--part); }
.sub.status h3::before { content: "⏳ "; }

a.xref { position: relative; font-weight: 600; text-decoration: none; color: var(--c, var(--accent)); background: color-mix(in srgb, var(--c, var(--accent)) 10%, transparent);
  padding: 0 .3em; border-radius: 4px; white-space: nowrap; }
a.xref:hover { background: color-mix(in srgb, var(--c, var(--accent)) 20%, transparent); }
a.xref::after { content: attr(data-tip); position: absolute; left: 50%; bottom: calc(100% + 8px); transform: translateX(-50%) translateY(4px); width: max-content; max-width: 280px;
  white-space: normal; background: var(--ink); color: var(--paper); font: 500 12.5px/1.35 var(--f-body); padding: 6px 9px; border-radius: 7px; pointer-events: none; opacity: 0; transition: .15s; z-index: 30; }
a.xref:hover::after, a.xref:focus-visible::after { opacity: 1; transform: translateX(-50%); }
.seealso { font-size: 14.5px; color: var(--ink-2); }

/* callouts */
.callout { margin: 16px 0; padding: 12px 18px; border-radius: 12px; border-left: 4px solid var(--c); background: color-mix(in srgb, var(--c) 7%, var(--sheet)); }
.callout p { margin: 6px 0; }
.callout.tldr { border-left-color: var(--signal); background: color-mix(in srgb, var(--signal) 9%, var(--sheet)); }

/* code */
.code { margin: 14px 0; border-radius: 12px; overflow: hidden; border: 1px solid var(--rule); background: #0f1a24; box-shadow: var(--shadow); }
.code-bar { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px 6px 14px; background: #16242f; color: #8fa6b8; font: 600 11.5px var(--f-mono); text-transform: uppercase; letter-spacing: .08em; }
.copy { border: 1px solid #2c3e4d; background: none; color: #c8d6e1; border-radius: 6px; padding: 2px 10px; cursor: pointer; font: 12px var(--f-body); text-transform: none; letter-spacing: 0; }
.copy:hover { background: #24384a; }
.code pre { margin: 0; padding: 14px 16px; overflow-x: auto; }
.code code { background: none; padding: 0; color: #d8e3ec; font-size: 13px; line-height: 1.55; overflow-wrap: normal; white-space: pre; }
.code .c-com { color: #7d93a5; font-style: italic; } .code .c-key { color: #7cc4ff; } .code .c-str { color: #f2c27b; } .code .c-kw { color: #d7a0ff; }

/* tables */
.tbl { margin: 14px 0 18px; border: 1px solid var(--rule); border-radius: 12px; background: var(--sheet); box-shadow: var(--shadow); overflow-x: auto; }
.tbl table { border-collapse: collapse; width: 100%; font-size: 14.5px; line-height: 1.5; }
.tbl th { text-align: left; font: 600 12px var(--f-display); letter-spacing: .07em; text-transform: uppercase; color: var(--ink-2);
  background: var(--sheet-2); padding: 10px 14px; border-bottom: 1px solid var(--rule); vertical-align: bottom; }
.tbl td { padding: 10px 14px; border-bottom: 1px solid var(--rule); vertical-align: top; overflow-wrap: break-word; }
.tbl tbody tr:last-child td { border-bottom: 0; }
.tbl tbody tr { transition: background .12s; }
.tbl tbody tr:hover { background: color-mix(in srgb, var(--c, var(--accent)) 5%, transparent); }
.tbl td:first-child { font-weight: 550; }
.tbl.cols-2 td:first-child { width: 34%; }
.tbl code { white-space: normal; }
.chip-s { display: inline-block; font: 600 12px/1.6 var(--f-body); padding: 0 9px; border-radius: 999px; white-space: nowrap; }
.chip-s.ok { color: var(--ok); background: var(--ok-bg); } .chip-s.part { color: var(--part); background: var(--part-bg); }
.chip-s.crit { color: var(--crit); background: var(--crit-bg); } .chip-s.none { color: var(--none); background: var(--none-bg); }
.chip-s.info { color: var(--info); background: var(--info-bg); }
/* narrow screens: every row becomes a card, each cell labelled by its column */
@media (max-width: 720px) {
  .tbl:not(.keep) { border: 0; background: none; box-shadow: none; overflow: visible; }
  .tbl:not(.keep) table, .tbl:not(.keep) tbody, .tbl:not(.keep) tr, .tbl:not(.keep) td { display: block; width: auto !important; }
  .tbl:not(.keep) thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .tbl:not(.keep) tr { background: var(--sheet); border: 1px solid var(--rule); border-left: 4px solid var(--c, var(--accent)); border-radius: 10px; margin-bottom: 10px; padding: 6px 0; box-shadow: var(--shadow); }
  .tbl:not(.keep) td { border: 0; padding: 4px 14px; }
  .tbl:not(.keep) td::before { content: attr(data-label); display: block; font: 600 11px var(--f-display); letter-spacing: .08em; text-transform: uppercase; color: var(--ink-2); }
  .tbl:not(.keep) td:first-child { font-size: 15.5px; padding-top: 6px; }
  .tbl:not(.keep) td:first-child::before { display: none; }
  .tbl:not(.keep) td:empty { display: none; }
}

/* prev / next */
.prevnext { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 30px; }
.pn { display: grid; padding: 12px 16px; border: 1px solid var(--rule); border-radius: 12px; text-decoration: none; color: var(--ink); background: var(--sheet); font-weight: 600; font-size: 14.5px; line-height: 1.35; }
.pn small { font-weight: 500; color: var(--ink-2); font-size: 12px; margin-bottom: 2px; }
.pn:hover { border-color: var(--c); }
.pn.next { text-align: right; }
@media (max-width: 560px) { .prevnext { grid-template-columns: 1fr; } .pn.next { text-align: left; } }
.foot { margin-top: 60px; padding-top: 18px; border-top: 1px solid var(--rule); font-size: 13px; color: var(--ink-2); }
.totop { position: fixed; right: 18px; bottom: 18px; z-index: 30; width: 44px; height: 44px; border-radius: 50%; border: 1px solid var(--rule); background: var(--sheet);
  box-shadow: var(--shadow); cursor: pointer; font-size: 18px; opacity: 0; pointer-events: none; transform: translateY(8px); transition: .2s; }
.totop.show { opacity: 1; pointer-events: auto; transform: none; }

/* ---------- figures ---------- */
.fig { margin: 22px 0 26px; border-radius: 16px; border: 1px solid color-mix(in srgb, var(--c) 35%, var(--rule)); background: var(--sheet); box-shadow: var(--shadow); overflow: hidden; }
.fig-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 12px 18px; border-bottom: 1px solid var(--rule);
  background: linear-gradient(90deg, color-mix(in srgb, var(--c) 12%, var(--sheet)), var(--sheet)); }
.fig-kicker { font: 600 10.5px var(--f-display); letter-spacing: .12em; text-transform: uppercase; color: #fff; background: var(--c); padding: 2px 8px; border-radius: 999px; }
.fig-title { font: 600 16px var(--f-display); }
.fig-body { padding: 16px 18px; }
.fig figcaption { padding: 0 18px 14px; font-size: 13px; color: var(--ink-2); }
.fig-ctl { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }
.btn { border: 1px solid var(--rule); background: var(--sheet); border-radius: 10px; padding: 7px 13px; cursor: pointer; font-size: 14px; line-height: 1.3; text-align: left; }
.btn:hover { border-color: var(--c); color: var(--c); }
.btn[aria-pressed="true"] { background: var(--c); border-color: var(--c); color: var(--sheet); }
.seg { display: inline-flex; border: 1px solid var(--rule); border-radius: 10px; overflow: hidden; }
.seg button { border: 0; background: var(--sheet); padding: 7px 13px; cursor: pointer; font-size: 13.5px; }
.seg button + button { border-left: 1px solid var(--rule); }
.seg button[aria-pressed="true"] { background: var(--c); color: var(--sheet); font-weight: 600; }
.std-toggle { display: inline-flex; align-items: center; gap: 9px; border: 1px solid var(--rule); background: var(--sheet); border-radius: 999px; padding: 5px 13px 5px 6px; cursor: pointer; font-size: 13.5px; }
.std-toggle .sw { width: 34px; height: 20px; border-radius: 999px; background: var(--none-bg); border: 1px solid var(--rule); position: relative; transition: .2s; }
.std-toggle .sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%; background: var(--ink-2); transition: .2s; }
.std-toggle[aria-pressed="true"] { border-color: var(--ok); color: var(--ok); font-weight: 600; }
.std-toggle[aria-pressed="true"] .sw { background: var(--ok); border-color: var(--ok); }
.std-toggle[aria-pressed="true"] .sw::after { left: 16px; background: #fff; }
.ctl-label { font: 600 11.5px var(--f-display); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-2); margin-bottom: 6px; }
.detail { margin-top: 12px; padding: 12px 14px; border-radius: 10px; background: var(--sheet-2); border: 1px solid var(--rule); font-size: 14.5px; min-height: 48px; }
.detail b { font-weight: 650; }
.legend { display: inline-flex; gap: 6px 12px; align-items: center; flex-wrap: wrap; font-size: 13px; color: var(--ink-2); }
.big { font: 600 20px var(--f-display); margin-left: auto; }

/* workflow */
.flow { list-style: none; margin: 0 0 14px; padding: 0; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; counter-reset: f; }
@media (max-width: 820px) { .flow { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.flow li { position: relative; padding: 12px; border-radius: 12px; border: 1px solid var(--rule); background: var(--sheet-2); font-size: 13.5px; line-height: 1.35; transition: .25s; }
.flow li .no { font: 600 22px var(--f-display); color: var(--ink-2); }
.flow li .t { font-weight: 600; display: block; margin: 2px 0 8px; }
.flow li.gate { border-style: dashed; border-color: var(--signal); }
.flow li.gate::after { content: "GATE"; position: absolute; top: 10px; right: 10px; font: 700 9.5px var(--f-display); letter-spacing: .1em; color: var(--signal); }
.flow li.on { border-color: var(--c); background: color-mix(in srgb, var(--c) 12%, var(--sheet)); transform: translateY(-3px); box-shadow: var(--shadow); }
.flow li.on .no { color: var(--c); }
.who { display: inline-block; width: 10px; height: 10px; border-radius: 3px; vertical-align: -1px; }
.who.se { background: var(--accent); } .who.auto { background: var(--ok); } .who.it { background: var(--t-doc); }
.whochip { font-size: 11.5px; font-weight: 600; padding: 1px 8px; border-radius: 999px; color: #fff; }
.whochip.se { background: var(--accent); } .whochip.auto { background: var(--ok); } .whochip.it { background: var(--t-doc); }
:root[data-theme="dark"] .whochip { color: #0d1217; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .whochip { color: #0d1217; } }

/* bars */
.bars { display: grid; gap: 8px; }
.barrow { display: grid; grid-template-columns: minmax(110px, 190px) minmax(0, 1fr) 92px; gap: 12px; align-items: center; font-size: 14px; border: 0; background: none; padding: 4px 6px; border-radius: 8px; text-align: left; cursor: pointer; }
.barrow:hover, .barrow[aria-pressed="true"] { background: var(--sheet-2); }
.barrow[aria-pressed="true"] .lab { font-weight: 650; color: var(--c); }
.track { height: 18px; border-radius: 6px; background: var(--paper); border: 1px solid var(--rule); overflow: hidden; }
.track i { display: block; height: 100%; width: 0; border-radius: 5px; background: linear-gradient(90deg, var(--c), color-mix(in srgb, var(--c) 60%, var(--signal))); transition: width .6s cubic-bezier(.2,.8,.2,1); }
.barrow .val { text-align: right; font: 12.5px var(--f-mono); font-variant-numeric: tabular-nums; }
@media (max-width: 520px) { .barrow { grid-template-columns: 1fr 76px; } .barrow .track { grid-column: 1 / -1; grid-row: 2; } }
.cards-out { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; min-height: 10px; }
.icard { font: 11.5px var(--f-mono); padding: 6px 8px; border-radius: 6px; background: var(--sheet); border: 1px solid var(--rule); border-top: 3px solid var(--c);
  box-shadow: var(--shadow); animation: pop .35s both; }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.96); } }

/* chevrons */
.chev { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding: 0; }
.chev li { flex: 1 1 120px; }
.chev button { width: 100%; border: 0; cursor: pointer; padding: 12px 22px 12px 26px; font-weight: 600; font-size: 14px; color: var(--ink);
  background: color-mix(in srgb, var(--c) 12%, var(--sheet)); clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 0 100%, 14px 50%); transition: .15s; }
.chev li:first-child button { clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 0 100%); padding-left: 16px; }
.chev button small { display: block; font-weight: 500; color: var(--ink-2); font-size: 11.5px; }
.chev button:hover, .chev button[aria-pressed="true"] { background: var(--c); color: var(--sheet); }
.chev button:hover small, .chev button[aria-pressed="true"] small { color: inherit; opacity: .85; }

/* vectors */
.vec { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 18px; align-items: start; }
@media (max-width: 760px) { .vec { grid-template-columns: minmax(0, 1fr); } }
.vec-plot svg { width: 100%; height: auto; display: block; border-radius: 12px; background: radial-gradient(circle at 50% 40%, color-mix(in srgb, var(--c) 6%, var(--sheet)), var(--sheet-2)); border: 1px solid var(--rule); }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { border: 1px solid var(--rule); background: var(--sheet); border-radius: 999px; padding: 6px 13px; cursor: pointer; font-size: 13.5px; text-align: left; }
.chip:hover { border-color: var(--c); }
.chip[aria-pressed="true"] { background: var(--c); border-color: var(--c); color: var(--sheet); }
.hits { margin: 0; padding-left: 20px; font-size: 13.5px; display: grid; gap: 6px; }
.hits li::marker { color: var(--c); font-weight: 700; }
.hits .sc { font: 600 12px var(--f-mono); color: var(--c); margin-left: 6px; }
.hits:empty::before { content: "Pick a question to search."; color: var(--ink-2); margin-left: -20px; }
svg text { font-family: var(--f-body); fill: var(--ink); }
.mono { font-family: var(--f-mono); font-size: .9em; }

/* graph */
.graph-wrap { overflow-x: auto; margin: 0 -6px; padding: 0 6px; }
.graph svg { min-width: 720px; width: 100%; height: auto; display: block; }
.graph .node rect { fill: var(--sheet); stroke-width: 1.6; transition: .25s; }
.graph .node { cursor: pointer; transition: opacity .25s; }
.graph .node .id { font: 600 11px var(--f-mono); }
.graph .node .nm { font-size: 10.5px; fill: var(--ink-2); }
.graph .k-top rect { stroke: var(--t-top); } .graph .k-sys rect { stroke: var(--t-sys); } .graph .k-sub rect { stroke: var(--t-sub); } .graph .k-doc rect { stroke: var(--t-doc); }
.graph .sc-dot { fill: var(--crit); }
.graph .edge { fill: none; stroke: var(--ink-2); stroke-width: 1.3; opacity: .5; transition: .25s; }
.graph .edge.doc { stroke-dasharray: 2 3; }
.graph .edge.iface { stroke: var(--signal); stroke-width: 2.4; opacity: .95; }
.graph .edge.cand { stroke-dasharray: 7 5; }
.graph .elbl { font: 600 10px var(--f-mono); fill: var(--signal); paint-order: stroke; stroke: var(--sheet); stroke-width: 4px; }
.graph svg.focus .node, .graph svg.focus .edge { opacity: .22; }
.graph svg.focus .node.hl, .graph svg.focus .edge.hl { opacity: 1; }
.graph .node.hl rect { stroke: var(--accent); stroke-width: 3; fill: color-mix(in srgb, var(--accent) 10%, var(--sheet)); }
.graph .edge.hl { stroke: var(--accent); stroke-width: 3; }
.graph .edge.iface.hl { stroke: var(--signal); stroke-width: 4; }
.graph .node.sel rect { stroke: var(--signal); stroke-width: 3; }
.graph .badge circle { fill: var(--accent); } .graph .badge text { fill: var(--sheet); font: 700 11px var(--f-mono); }
.walk-out { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 14px; align-items: start; margin-top: 10px; }
@media (max-width: 760px) { .walk-out { grid-template-columns: minmax(0, 1fr); } }
.walk-info { padding: 12px 14px; border-radius: 10px; background: var(--sheet-2); border: 1px solid var(--rule); font-size: 14px; }
.walk-tbl td, .walk-tbl th { font-size: 13px; padding: 6px 10px; }
.walk-tbl td { font-family: var(--f-mono); font-size: 12.5px; }
.walk-tbl tr { animation: pop .3s both; }
.walk-out .tbl { margin: 0; }
.walk-tbl .ph td { font-family: var(--f-body); color: var(--ink-2); }

/* coverage */
.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
@media (max-width: 620px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.tile { padding: 10px 14px; border-radius: 12px; border: 1px solid var(--rule); background: var(--sheet-2); }
.tile .k { font: 600 11px var(--f-display); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-2); }
.tile .v { font: 600 28px/1.15 var(--f-display); font-variant-numeric: tabular-nums; }
.tile.bad .v { color: var(--crit); } .tile.good .v { color: var(--ok); }
.tbl.cov { margin: 0; }
.cov th, .cov td { text-align: center; }
.cov th:first-child, .cov td:first-child { text-align: left; }
.cell { display: inline-grid; place-items: center; width: 34px; height: 28px; border-radius: 7px; font-weight: 700; transition: .3s; }
.cell.has { color: var(--ok); background: var(--ok-bg); }
.cell.miss { color: var(--crit); background: var(--crit-bg); }
.cell.miss.sc { outline: 2px solid var(--crit); }
.cell.nr { color: var(--ink-2); opacity: .55; }
.cell.new { animation: flash 1s; }
@keyframes flash { 0% { transform: scale(1.4); box-shadow: 0 0 0 6px color-mix(in srgb, var(--ok) 35%, transparent); } }
.cov .sub-n { font-weight: 600; display: block; }
.cov .sub-a { font-size: 12px; color: var(--ink-2); font-weight: 400; }
@media (max-width: 720px) { .tbl.cov td { display: flex !important; justify-content: space-between; align-items: center; } .tbl.cov td:first-child { display: block !important; } .tbl.cov td::before { display: inline !important; } }
.rollup { display: grid; gap: 6px; }
.rollup .barrow { cursor: default; }
.rollup .barrow.total .lab { font-weight: 700; }

/* gap decision */
.decide { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.decide li { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; align-items: start; }
.decide .dot { width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; font: 700 14px var(--f-mono); color: #fff; background: var(--c); }
.decide .q { font-style: italic; }
.decide .box { padding: 10px 14px; border-radius: 10px; border: 1px solid var(--rule); background: var(--sheet-2); font-size: 14.5px; }
.decide .branch { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 620px) { .decide .branch { grid-template-columns: 1fr; } }
.decide .br { padding: 10px 14px; border-radius: 10px; border: 1px dashed var(--rule); font-size: 14px; opacity: .45; transition: .3s; }
.decide .br.on { opacity: 1; border-style: solid; box-shadow: var(--shadow); }
.decide .br.gapb.on { border-color: var(--crit); background: var(--crit-bg); }
.decide .br.okb.on { border-color: var(--ok); background: var(--ok-bg); }
.decide .br h4 { margin: 0 0 4px; font: 600 13px var(--f-display); letter-spacing: .06em; text-transform: uppercase; }
.decide .br p { margin: 0; }

/* traceability */
.trow { display: grid; grid-template-columns: 170px minmax(0, 1fr); gap: 12px; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--rule); }
.trow:last-child { border-bottom: 0; }
@media (max-width: 560px) { .trow { grid-template-columns: 1fr; } }
.trow .nm { font-weight: 600; font-size: 14px; } .trow .nm small { display: block; color: var(--ink-2); font-weight: 400; }
.reqs { display: flex; flex-wrap: wrap; gap: 6px; }
.req { font: 600 11.5px var(--f-mono); padding: 5px 9px; border-radius: 8px; cursor: pointer; border: 1.5px solid var(--crit); color: var(--crit); background: var(--sheet); transition: .3s; }
.req.ver { border-color: var(--ok); background: var(--ok); color: var(--sheet); }
.req[aria-pressed="true"] { box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 40%, transparent); }

/* swci */
.swci { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 820px) { .swci { grid-template-columns: minmax(0, 1fr); } }
.swci .tbl { margin: 0; }
.swci th, .swci td { text-align: center; font-size: 13px; padding: 7px 8px; }
.swci td:first-child, .swci th:first-child { text-align: left; }
.swci td:first-child small { display: block; color: var(--ink-2); font-weight: 400; font-size: 11.5px; }
.sw-cell { border: 0; border-radius: 8px; padding: 6px 4px; width: 100%; cursor: pointer; font: 600 12.5px var(--f-mono); }
.sw-cell.l1 { background: var(--crit-bg); color: var(--crit); } .sw-cell.l2 { background: color-mix(in srgb, var(--signal) 18%, var(--sheet)); color: var(--signal); }
.sw-cell.l3 { background: var(--part-bg); color: var(--part); } .sw-cell.l4 { background: var(--info-bg); color: var(--info); } .sw-cell.l5 { background: var(--none-bg); color: var(--none); }
.sw-cell[aria-pressed="true"] { outline: 3px solid var(--ink); outline-offset: -1px; }
.swci-out { padding: 16px; border-radius: 12px; background: var(--sheet-2); border: 1px solid var(--rule); }
.swci-out .res { font: 600 34px/1 var(--f-display); margin: 4px 0 8px; }
.swci-out p { margin: 8px 0 0; font-size: 14px; }
.swci-out .src { font-size: 12px; color: var(--ink-2); }

/* review pipeline */
.pipe { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
@media (max-width: 820px) { .pipe { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.pipe button { width: 100%; height: 100%; text-align: left; border: 1px solid var(--rule); background: var(--sheet-2); border-radius: 12px; padding: 10px 12px; cursor: pointer; font-size: 13.5px; line-height: 1.3; }
.pipe button .ic { font-size: 20px; display: block; margin-bottom: 4px; }
.pipe button small { display: block; color: var(--ink-2); font-size: 11.5px; margin-top: 3px; }
.pipe button:hover, .pipe button[aria-pressed="true"] { border-color: var(--c); background: color-mix(in srgb, var(--c) 12%, var(--sheet)); }
.pipe .human button { border: 2px solid var(--signal); background: color-mix(in srgb, var(--signal) 10%, var(--sheet)); }
.pipe .agent button small::before { content: "agent · "; }

/* print */
@media print {
  .topbar, .side, .totop, .fig-ctl, .search, .prevnext, .hlink, .scrim { display: none !important; }
  .layout { display: block; } .hero { color: #000; background: none; } .hero h1, .meta-card strong { color: #000; }
  .chap { break-before: page; } .fig, .tbl, .code { break-inside: avoid; box-shadow: none; }
}
"""

JS = r"""
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const NS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs = {}, parent) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (parent) parent.appendChild(n); return n; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const sleep = ms => new Promise(r => setTimeout(r, reduced ? 0 : ms));

/* ---------- theme ---------- */
const themes = ['auto', 'light', 'dark'], icons = { auto: '◐', light: '☀', dark: '☾' };
const themeBtn = $('.theme');
function applyTheme(t) {
  if (t === 'auto') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t);
  themeBtn.textContent = icons[t]; themeBtn.title = 'Theme: ' + t; store.set('esam-theme', t);
}
applyTheme(store.get('esam-theme') || 'auto');
themeBtn.onclick = () => applyTheme(themes[(themes.indexOf(store.get('esam-theme') || 'auto') + 1) % 3]);

/* ---------- drawer ---------- */
const menuBtn = $('.menu');
const setNav = open => { document.body.classList.toggle('nav-open', open); menuBtn.setAttribute('aria-expanded', open); $('.scrim').hidden = !open; };
menuBtn.onclick = () => setNav(!document.body.classList.contains('nav-open'));
$('.scrim').onclick = () => setNav(false);
$('.side').addEventListener('click', e => { if (e.target.closest('a') && innerWidth <= 1080) setNav(false); });

/* ---------- progress, scrollspy, back to top ---------- */
const bar = $('.progress i'), toTop = $('.totop'), crumb = $('.crumb');
const chaps = $$('.chap'), tocItems = $$('.toc > ol > li');
const subLinks = $$('.toc li ol a'), h3s = $$('.chap h3[id]');
function onScroll() {
  const h = document.documentElement, max = h.scrollHeight - h.clientHeight;
  bar.style.width = (max > 0 ? h.scrollTop / max * 100 : 0) + '%';
  toTop.classList.toggle('show', h.scrollTop > 800);
  const y = scrollY + 120;
  let ci = -1; chaps.forEach((c, i) => { if (c.offsetTop <= y) ci = i; });
  tocItems.forEach((li, i) => li.classList.toggle('active', i === ci));
  crumb.textContent = ci >= 0 ? chaps[ci].dataset.ch + '. ' + chaps[ci].dataset.title : '';
  let cur = null; h3s.forEach(h => { if (h.getBoundingClientRect().top + scrollY <= y) cur = h.id; });
  subLinks.forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + cur));
}
addEventListener('scroll', () => requestAnimationFrame(onScroll), { passive: true });
onScroll();
toTop.onclick = () => scrollTo({ top: 0 });

/* ---------- heading links, code copy ---------- */
async function copy(text) { try { await navigator.clipboard.writeText(text); return true; } catch { return false; } }
document.addEventListener('click', async e => {
  const h = e.target.closest('.hlink');
  if (h) { e.preventDefault(); history.replaceState(null, '', h.getAttribute('href')); h.closest('h2,h3').scrollIntoView();
    if (await copy(location.href)) { h.classList.add('copied'); setTimeout(() => h.classList.remove('copied'), 1400); } }
  const c = e.target.closest('.copy');
  if (c) { const ok = await copy(c.closest('.code').querySelector('code').innerText); c.textContent = ok ? 'Copied ✓' : 'Select + copy'; setTimeout(() => c.textContent = 'Copy', 1400); }
});

/* tiny syntax colouring for yaml / sql code blocks */
$$('.code').forEach(b => {
  const lang = b.querySelector('.code-bar span').textContent, code = b.querySelector('code');
  let s = code.innerHTML;
  if (lang === 'yaml') s = s.split('\n').map(l => l
      .replace(/(#[^\n]*)$/, '<span class="c-com">$1</span>')
      .replace(/^(\s*-?\s*)([\w]+)(:)/, '$1<span class="c-key">$2</span>$3')
      .replace(/(&quot;[^&]*?&quot;)/g, '<span class="c-str">$1</span>')).join('\n');
  else if (lang === 'sql') s = s.split('\n').map(l => l
      .replace(/(--[^\n]*)$/, '<span class="c-com">$1</span>')
      .replace(/\b(WITH RECURSIVE|SELECT|FROM|WHERE|JOIN|UNION ALL|AS|NOT|ANY)\b/g, '<span class="c-kw">$1</span>')
      .replace(/('[^']*')/g, '<span class="c-str">$1</span>')).join('\n');
  code.innerHTML = s;
});

/* ---------- search ---------- */
const q = $('#q'), snav = $('.search-nav'), main = $('#main');
let hits = [], hi = -1, qt;
function clearHits() { $$('mark.hit', main).forEach(m => { const p = m.parentNode; p.replaceChild(document.createTextNode(m.textContent), m); p.normalize(); }); hits = []; hi = -1; }
function runSearch() {
  clearHits(); const term = q.value.trim();
  if (term.length < 2) { snav.hidden = true; return; }
  const src = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), re = new RegExp(src, 'gi'), probe = new RegExp(src, 'i');
  const walker = document.createTreeWalker($('.content'), NodeFilter.SHOW_TEXT, { acceptNode: n =>
    n.parentElement.closest('svg,script,style,button,.fig-ctl,.cards') || !probe.test(n.data) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(n => {
    const frag = document.createDocumentFragment(); let last = 0; re.lastIndex = 0; let m;
    while ((m = re.exec(n.data))) { frag.append(n.data.slice(last, m.index)); const mk = document.createElement('mark'); mk.className = 'hit'; mk.textContent = m[0]; frag.append(mk); hits.push(mk); last = m.index + m[0].length; }
    frag.append(n.data.slice(last)); n.parentNode.replaceChild(frag, n);
  });
  snav.hidden = false; $('.count', snav).textContent = hits.length ? hits.length + ' match' + (hits.length > 1 ? 'es' : '') : 'No matches';
  if (hits.length) go(0, false);
}
function go(i, scroll = true) {
  if (!hits.length) return; hits[hi]?.classList.remove('cur'); hi = (i + hits.length) % hits.length; hits[hi].classList.add('cur');
  $('.count', snav).textContent = (hi + 1) + ' of ' + hits.length;
  if (scroll) hits[hi].scrollIntoView({ block: 'center' });
}
q.addEventListener('input', () => { clearTimeout(qt); qt = setTimeout(runSearch, 180); });
q.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); go(hi + (e.shiftKey ? -1 : 1)); if (innerWidth <= 1080) setNav(false); } if (e.key === 'Escape') { q.value = ''; runSearch(); q.blur(); } });
snav.addEventListener('click', e => { const b = e.target.closest('[data-hit]'); if (b) go(hi + +b.dataset.hit); });
const openSearch = () => { if (innerWidth <= 1080) setNav(true); q.focus(); q.select(); };
$('.searchbtn').onclick = openSearch;
addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); openSearch(); } if (e.key === 'Escape') setNav(false); });

/* ---------- shared scenario: has STD-BRK-001 been loaded? ---------- */
const std = { on: false, subs: [], set(v) { this.on = v; $$('.std-toggle').forEach(b => b.setAttribute('aria-pressed', v)); this.subs.forEach(f => f(true)); } };
document.addEventListener('click', e => { if (e.target.closest('.std-toggle')) std.set(!std.on); });

/* ================= figures ================= */

/* §1.3 workflow */
(() => {
  const ol = $('[data-flow]'); if (!ol) return;
  const steps = [['SE + converter', 'se', 'Export the contract WBS → skeleton YAML'], ['SE', 'se', 'Add SE attributes, functions, interfaces, documents'],
    ['Automatic', 'auto', 'Schema check catches errors before commit', 1], ['SE', 'se', 'Commit to Git with a meaningful message'],
    ['IT/SW', 'it', 'Ingestion updates the graph database'], ['SE', 'se', 'Review the Diff Matrix → Approve and Lock Baseline', 1]];
  ol.innerHTML = steps.map((s, i) => `<li class="${s[3] ? 'gate' : ''}"><span class="no">${i + 1}</span><span class="t">${s[2]}</span><span class="whochip ${s[1]}">${s[0]}</span></li>`).join('');
  let busy = false;
  $('[data-play]').onclick = async () => { if (busy) return; busy = true; const li = $$('li', ol);
    for (const l of li) { li.forEach(x => x.classList.remove('on')); l.classList.add('on'); await sleep(750); }
    await sleep(600); li.forEach(x => x.classList.remove('on')); busy = false; };
})();

/* §2.2 rows */
(() => {
  const box = $('[data-rows]'); if (!box) return;
  const docs = [['ICD-BRK-001', 8], ['SDD-BRK-001', 6], ['SDD-DCL-001', 6], ['SRS-BRK-001', 4], ['SRS-DCL-001', 4]];
  box.innerHTML = docs.map(([d, n]) => `<button class="barrow" aria-pressed="false" data-d="${d}" data-n="${n}"><span class="lab mono">${d}</span><span class="track"><i style="width:${n / 8 * 100}%"></i></span><span class="val">${n} rows</span></button>`).join('')
    + `<div class="barrow" style="cursor:default"><span class="lab"><b>Total</b></span><span></span><span class="val"><b>28 rows</b></span></div>`;
  const out = $('[data-cards]');
  box.addEventListener('click', e => { const b = e.target.closest('button.barrow'); if (!b) return;
    $$('button.barrow', box).forEach(x => x.setAttribute('aria-pressed', x === b));
    out.innerHTML = Array.from({ length: +b.dataset.n }, (_, i) => `<span class="icard" style="animation-delay:${i * 45}ms">${b.dataset.d}::${String(i).padStart(4, '0')}</span>`).join(''); });
})();

/* §2.3 chunking steps */
(() => {
  const ol = $('[data-chev]'); if (!ol) return;
  const st = [['Convert', 'Word headings → section tree', 'The section tree is how every chunk knows where it sits.'],
    ['Clean', 'Drop TOC, revision history, headers', 'Otherwise the table of contents matches every search.'],
    ['Split', 'Sections, then requirement / test IDs', 'One chunk per requirement or test case is far more useful than an arbitrary slice.'],
    ['Label', 'Prefix the full section path', 'On page 600, “the rate shall be 100 ms” means nothing without its label: <code>SDD-BRK-001 › 3.2 Wheel-slip control › 3.2.4 Pressure modulation</code>'],
    ['Tag', 'Record IDs a chunk mentions', 'These become links in the graph: <code>verifies: SRS-BRK-012</code> means this test <i>verifies</i> that requirement.']];
  ol.innerHTML = st.map((s, i) => `<li><button aria-pressed="${i === 0}" data-i="${i}">${s[0]}<small>${s[1]}</small></button></li>`).join('');
  const d = $('[data-chev-detail]'); const show = i => { d.innerHTML = `<b>${st[i][0]}.</b> ${st[i][2]}`; $$('button', ol).forEach((b, j) => b.setAttribute('aria-pressed', j === i)); };
  ol.addEventListener('click', e => { const b = e.target.closest('button'); if (b) show(+b.dataset.i); }); show(0);
})();

/* §3 map of meaning */
(() => {
  const host = $('[data-vplot]'); if (!host) return;
  const P = [
    ['ICD-BRK-001 › 3.1 EBC1 — Electronic Brake Controller 1', 27, 30, 'icd'], ['ICD-BRK-001 › 4. Timing and loss of communication', 37, 40, 'icd'],
    ['SRS-BRK-001 › 3. Interface requirements', 19, 38, 'srs'], ['SRS-BRK-001 › 1. Control requirements', 20, 56, 'srs'], ['SRS-BRK-001 › 2. Safety requirements', 31, 63, 'srs'],
    ['SDD-BRK-001 › 4. Fail-safe behaviour', 40, 58, 'sdd'], ['SDD-BRK-001 › 2. Task set', 48, 48, 'sdd'],
    ['SRS-DCL-001 › 2. Brake warning requirements', 56, 33, 'srs'], ['SDD-DCL-001 › 3. Brake-related displays', 62, 24, 'sdd'],
    ['SRS-DCL-001 › 3. Night operations requirements', 82, 20, 'srs'], ['SDD-DCL-001 › 4. Night operations', 87, 30, 'sdd'],
    ['SRS-DCL-001 › 1. Display requirements', 74, 40, 'srs'], ['SDD-DCL-001 › 5. Boot and update', 80, 54, 'sdd'],
    ['ICD-BRK-001 › 2. Physical layer', 44, 80, 'icd'], ['SDD-BRK-001 › 5. Power and environment', 56, 84, 'sdd'], ['SDD-BRK-001 › 3. Memory and partitioning', 64, 72, 'sdd']];
  const Q = [['ABS status broadcast', 28, 34], ['NVG / blackout display mode', 84, 25], ['What warns the driver about brake faults?', 58, 29], ['How fast must pressure release on lock-up?', 26, 58]];
  const W = 560, H = 380, X = x => 20 + x / 100 * (W - 40), Y = y => 20 + y / 100 * (H - 40);
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Chunks placed on a two-dimensional map of meaning' }, host);
  const col = { icd: 'var(--t-sys)', srs: 'var(--ok)', sdd: 'var(--t-doc)' };
  [['Brake & EBC1', 30, 47], ['Driver display', 75, 36], ['Platform & power', 54, 78]].forEach(([t, x, y]) =>
    svgEl('ellipse', { cx: X(x), cy: Y(y), rx: 95, ry: 70, fill: 'var(--none-bg)', opacity: .55 }, svg));
  [['Brake & EBC1', 8, 12], ['Driver display', 70, 8], ['Platform & power', 30, 96]].forEach(([t, x, y]) => {
    const tx = svgEl('text', { x: X(x), y: Y(y), 'font-size': 11, 'font-weight': 600, fill: 'var(--ink-2)', 'letter-spacing': '.08em' }, svg); tx.textContent = t.toUpperCase(); });
  const lines = svgEl('g', {}, svg), dots = svgEl('g', {}, svg), qg = svgEl('g', {}, svg);
  P.forEach(p => { const c = svgEl('circle', { cx: X(p[1]), cy: Y(p[2]), r: 7, fill: col[p[3]], stroke: 'var(--sheet)', 'stroke-width': 2 }, dots); svgEl('title', {}, c).textContent = p[0]; p.el = c; });
  const lg = svgEl('g', {}, svg); [['ICD', 'icd'], ['SRS', 'srs'], ['SDD', 'sdd']].forEach(([t, k], i) => {
    svgEl('circle', { cx: W - 120 + i * 40, cy: H - 14, r: 5, fill: col[k] }, lg); const tx = svgEl('text', { x: W - 112 + i * 40, y: H - 10, 'font-size': 11 }, lg); tx.textContent = t; });
  const chips = $('[data-vq]'), list = $('[data-vhits]');
  chips.innerHTML = Q.map((q, i) => `<button class="chip" aria-pressed="false" data-i="${i}">${q[0]}</button>`).join('');
  chips.addEventListener('click', e => { const b = e.target.closest('.chip'); if (!b) return;
    $$('.chip', chips).forEach(x => x.setAttribute('aria-pressed', x === b));
    const [label, qx, qy] = Q[+b.dataset.i];
    const ranked = P.map(p => ({ p, d: Math.hypot(p[1] - qx, (p[2] - qy) * .7) })).sort((a, b) => a.d - b.d);
    lines.innerHTML = qg.innerHTML = ''; P.forEach(p => p.el.setAttribute('r', 7));
    ranked.slice(0, 3).forEach(({ p }, i) => { svgEl('line', { x1: X(qx), y1: Y(qy), x2: X(p[1]), y2: Y(p[2]), stroke: 'var(--signal)', 'stroke-width': 2.2 - i * .5, 'stroke-dasharray': '4 3' }, lines); p.el.setAttribute('r', 10); });
    const star = svgEl('path', { d: 'M0,-12 L3.5,-4 12,-4 5,2 7.5,11 0,5.5 -7.5,11 -5,2 -12,-4 -3.5,-4Z', transform: `translate(${X(qx)},${Y(qy)})`, fill: 'var(--signal)', stroke: 'var(--sheet)', 'stroke-width': 1.5 }, qg);
    svgEl('title', {}, star).textContent = 'Your question: ' + label;
    list.innerHTML = ranked.slice(0, 3).map(({ p, d }) => `<li>${esc(p[0])}<span class="sc">${Math.max(0, 1 - d / 90).toFixed(2)}</span></li>`).join(''); });
})();

/* §4 graph walk */
(() => {
  const host = $('[data-graph]'); if (!host) return;
  const N = {
    'V-001': ['SYNTH-LTV Ground Vehicle System', 'top', 480, 30, '1.0'], 'FAM-001': ['SYNTH-LTV Family of Vehicles', 'top', 480, 100, '1.1'],
    'VAR-001': ['Lead Variant (Utility)', 'top', 480, 170, '1.1.1'],
    'SYS-SUSP': ['Suspension/Steering', 'sys', 170, 250, '1.1.1.5'], 'SYS-VETR': ['Vehicle Electronics', 'sys', 520, 250, '1.1.1.6'], 'SYS-PWR': ['Power Pack/Drive Train', 'sys', 830, 250, '1.1.1.7'],
    'SUB-BRK-ECU': ['Brake Control ECU (ABS)', 'sub', 170, 345, '', 1], 'SUB-J1939-BB': ['J1939 Vehicle Data Backbone', 'sub', 420, 345, '', 1],
    'SUB-DCL': ['Driver Digital Cluster', 'sub', 625, 345, ''], 'SUB-ENG-ECM': ['Engine Control Module', 'sub', 830, 345, ''],
    'ICD-BRK-001': ['Brake ECU J1939 Interface Control Document', 'doc', 66, 440], 'SDD-BRK-001': ['Brake Control ECU Software Design', 'doc', 170, 440],
    'SRS-BRK-001': ['Brake Control ECU Software Requirements', 'doc', 274, 440], 'SDD-DCL-001': ['Driver Digital Cluster Software Design', 'doc', 573, 440],
    'SRS-DCL-001': ['Driver Digital Cluster Software Requirements', 'doc', 677, 440] };
  const PART = [['FAM-001', 'V-001'], ['VAR-001', 'FAM-001'], ['SYS-SUSP', 'VAR-001'], ['SYS-VETR', 'VAR-001'], ['SYS-PWR', 'VAR-001'],
    ['SUB-BRK-ECU', 'SYS-SUSP'], ['SUB-J1939-BB', 'SYS-VETR'], ['SUB-DCL', 'SYS-VETR'], ['SUB-ENG-ECM', 'SYS-PWR']];
  const DOC = [['SUB-BRK-ECU', 'ICD-BRK-001'], ['SUB-BRK-ECU', 'SDD-BRK-001'], ['SUB-BRK-ECU', 'SRS-BRK-001'], ['SUB-DCL', 'SDD-DCL-001'], ['SUB-DCL', 'SRS-DCL-001']];
  const IF = [['SUB-BRK-ECU', 'SUB-J1939-BB', 'confirmed'], ['SUB-J1939-BB', 'SUB-DCL', 'confirmed'], ['SUB-J1939-BB', 'SUB-ENG-ECM', 'candidate']];
  const size = k => k === 'doc' ? [96, 30] : [150, 42];
  const svg = svgEl('svg', { viewBox: '0 0 960 470', role: 'img', 'aria-label': 'SYNTH-LTV baseline graph' }, host);
  const eg = svgEl('g', {}, svg), ng = svgEl('g', {}, svg), bg = svgEl('g', {}, svg);
  const E = {};
  const key = (a, b) => a + '|' + b;
  const vline = (a, b, cls) => { const [, , ax, ay] = N[a], [, kb, bx, by] = N[b], [, ka] = N[a];
    const y1 = ay - size(ka)[1] / 2, y2 = by + size(kb)[1] / 2, my = (y1 + y2) / 2;
    E[key(a, b)] = E[key(b, a)] = svgEl('path', { class: 'edge ' + cls, d: `M${ax},${y1} C${ax},${my} ${bx},${my} ${bx},${y2}` }, eg); };
  PART.forEach(([c, p]) => vline(c, p, ''));
  DOC.forEach(([s, d]) => vline(d, s, 'doc'));
  IF.forEach(([a, b, st]) => { const [, , ax, ay] = N[a], [, , bx, by] = N[b]; let d, lx, ly;
    if (b === 'SUB-ENG-ECM') { d = `M${ax},${ay - 21} Q${(ax + bx) / 2},${ay - 70} ${bx},${by - 21}`; lx = (ax + bx) / 2; ly = ay - 50; }
    else { d = `M${ax + 75},${ay} L${bx - 75},${by}`; lx = (ax + bx) / 2; ly = ay - 8; }
    E[key(a, b)] = E[key(b, a)] = svgEl('path', { class: 'edge iface' + (st === 'candidate' ? ' cand' : ''), d, 'marker-end': 'url(#arr)' }, eg);
    const t = svgEl('text', { class: 'elbl', x: lx, y: ly, 'text-anchor': 'middle' }, eg); t.textContent = 'EBC1' + (st === 'candidate' ? ' (candidate)' : ''); });
  const defs = svgEl('defs', {}, svg), mk = svgEl('marker', { id: 'arr', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
  svgEl('path', { d: 'M0,0 L10,5 L0,10z', fill: 'var(--signal)' }, mk);
  const G = {};
  for (const [id, [nm, k, x, y, wbs, sc]] of Object.entries(N)) {
    const [w, h] = size(k), g = svgEl('g', { class: `node k-${k}`, tabindex: 0, role: 'button', 'aria-label': `${id} ${nm}` }, ng);
    svgEl('rect', { x: x - w / 2, y: y - h / 2, width: w, height: h, rx: 8 }, g);
    const t1 = svgEl('text', { class: 'id', x, y: k === 'doc' ? y + 4 : y - 3, 'text-anchor': 'middle' }, g); t1.textContent = id;
    if (k !== 'doc') { const t2 = svgEl('text', { class: 'nm', x, y: y + 12, 'text-anchor': 'middle' }, g); t2.textContent = nm.length > 24 ? nm.slice(0, 22) + '…' : nm; }
    if (sc) { const c = svgEl('circle', { class: 'sc-dot', cx: x + w / 2 - 8, cy: y - h / 2 + 8, r: 4 }, g); svgEl('title', {}, c).textContent = 'safety-critical'; }
    g.onclick = () => info(id); g.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); info(id); } };
    G[id] = g;
  }
  const winfo = $('[data-winfo]'), rows = $('[data-wrows]');
  function info(id) {
    const [nm, k, , , wbs, sc] = N[id];
    $$('.node', svg).forEach(n => n.classList.remove('sel')); G[id].classList.add('sel');
    const kind = { top: 'WBS level', sys: 'System', sub: 'Subsystem', doc: 'Document' }[k];
    const ifs = IF.filter(i => i[0] === id || i[1] === id).map(i => `${i[0] === id ? 'sends EBC1 to' : 'receives EBC1 from'} <code>${i[0] === id ? i[1] : i[0]}</code> (${i[2]})`);
    winfo.innerHTML = `<b>${esc(nm)}</b><br><code>${id}</code> · ${kind}${wbs ? ' · WBS ' + wbs : ''}${sc ? ' · <span class="chip-s crit">safety-critical</span>' : ''}${ifs.length ? '<br>' + ifs.join('<br>') : ''}`;
  }
  const WALKS = {
    up: { info: '<b>Up the tree.</b> Start at the document, follow <i>has document</i> once, then <i>part of</i> until there is no parent.',
      steps: [['ICD-BRK-001', null, 'start'], ['SUB-BRK-ECU', 'ICD-BRK-001', 'has document'], ['SYS-SUSP', 'SUB-BRK-ECU', 'part of'], ['VAR-001', 'SYS-SUSP', 'part of'], ['FAM-001', 'VAR-001', 'part of'], ['V-001', 'FAM-001', 'part of']] },
    down: { info: '<b>Down the tree.</b> Start at the system, follow <i>part of</i> and <i>has document</i> downward until nothing new turns up.',
      steps: [['SYS-SUSP', null, 'start'], ['SUB-BRK-ECU', 'SYS-SUSP', 'part of'], ['ICD-BRK-001', 'SUB-BRK-ECU', 'has document', 2], ['SDD-BRK-001', 'SUB-BRK-ECU', 'has document', 2], ['SRS-BRK-001', 'SUB-BRK-ECU', 'has document', 2]] },
    across: { info: '<b>Across interfaces.</b> Start at the changed subsystem and follow <i>sends EBC1</i>. The Engine ECM link is still a <i>candidate</i>: an SE must confirm it.',
      steps: [['SUB-BRK-ECU', null, 'start'], ['SUB-J1939-BB', 'SUB-BRK-ECU', 'EBC1 (confirmed)'], ['SUB-DCL', 'SUB-J1939-BB', 'EBC1 (confirmed)', 2], ['SUB-ENG-ECM', 'SUB-J1939-BB', 'EBC1 (candidate)', 2]] } };
  let run = 0;
  async function walk(name) {
    const my = ++run, w = WALKS[name];
    $$('[data-walk]').forEach(b => b.setAttribute('aria-pressed', b.dataset.walk === name));
    svg.classList.add('focus'); $$('.hl', svg).forEach(n => n.classList.remove('hl')); bg.innerHTML = ''; rows.innerHTML = '';
    winfo.innerHTML = w.info;
    let depth = 0;
    for (const [id, from, via, d] of w.steps) {
      if (my !== run) return;
      depth = d ?? (from ? depth + 1 : 0);
      if (from) E[key(from, id)].classList.add('hl');
      G[id].classList.add('hl');
      const [, k, x, y] = N[id], [wd, h] = size(k);
      const b = svgEl('g', { class: 'badge' }, bg); svgEl('circle', { cx: x - wd / 2, cy: y - h / 2, r: 10 }, b);
      const t = svgEl('text', { x: x - wd / 2, y: y - h / 2 + 4, 'text-anchor': 'middle' }, b); t.textContent = depth;
      rows.insertAdjacentHTML('beforeend', `<tr><td data-label="depth">${depth}</td><td data-label="id">${id}</td><td data-label="name">${esc(N[id][0])}</td><td data-label="link followed">${via}</td></tr>`);
      await sleep(650);
    }
    if (my === run) rows.insertAdjacentHTML('beforeend', `<tr><td colspan="4" data-label="" style="font-family:var(--f-body);color:var(--ink-2)">Nowhere new to go: ${w.steps.length} rows, and nobody said how many levels there were.</td></tr>`);
  }
  $$('[data-walk]').forEach(b => b.onclick = () => walk(b.dataset.walk));
})();

/* §5.2 coverage */
(() => {
  const root = $('#fig-coverage'); if (!root) return;
  const TYPES = ['SRS', 'SDD', 'STD', 'ICD'];
  const SUBS = [
    { id: 'SUB-BRK-ECU', name: 'Brake ECU', about: 'software · safety-critical · interfaces', sys: 0, sw: 1, sc: 1, has: ['SRS', 'SDD', 'ICD'] },
    { id: 'SUB-J1939-BB', name: 'J1939 backbone', about: 'safety-critical · interfaces', sys: 1, sw: 0, sc: 1, has: [] },
    { id: 'SUB-DCL', name: 'Driver cluster', about: 'software · interfaces', sys: 1, sw: 1, sc: 0, has: ['SRS', 'SDD'] },
    { id: 'SUB-ENG-ECM', name: 'Engine ECM', about: 'interfaces', sys: 2, sw: 0, sc: 0, has: [] }];
  const SYS = ['Suspension/Steering', 'Vehicle Electronics', 'Power Pack/Drive Train'];
  let mode = 'rules';
  const req = s => mode === 'fixed' ? TYPES : TYPES.filter(t => (t === 'SRS' || t === 'SDD') ? s.sw : t === 'STD' ? s.sw && s.sc : true);
  const has = s => new Set(std.on && s.id === 'SUB-BRK-ECU' ? [...s.has, 'STD'] : s.has);
  function render(fromStd) {
    let R = 0, P = 0, SC = 0; const sysR = [0, 0, 0], sysP = [0, 0, 0];
    const body = SUBS.map(s => { const r = req(s), h = has(s);
      return `<tr><td data-label=""><span class="sub-n">${s.name}</span><span class="sub-a">${s.about}</span></td>` + TYPES.map(t => {
        if (!r.includes(t)) return `<td data-label="${t}"><span class="cell nr" title="not required">–</span></td>`;
        R++; sysR[s.sys]++;
        if (h.has(t)) { P++; sysP[s.sys]++; const fresh = fromStd && std.on && t === 'STD' && s.id === 'SUB-BRK-ECU';
          return `<td data-label="${t}"><span class="cell has${fresh ? ' new' : ''}" title="present">✓</span></td>`; }
        if (s.sc) SC++;
        return `<td data-label="${t}"><span class="cell miss${s.sc ? ' sc' : ''}" title="missing${s.sc ? ' (safety-critical)' : ''}">✗${s.sc ? '⚠' : ''}</span></td>`; }).join('') + '</tr>'; }).join('');
    $('[data-cmatrix]', root).innerHTML = `<thead><tr><th>Subsystem</th>${TYPES.map(t => `<th>${t}</th>`).join('')}</tr></thead><tbody>${body}</tbody>`;
    $('[data-ctiles]', root).innerHTML = [['Required', R], ['Present', P, 'good'], ['Missing', R - P, R - P ? 'bad' : 'good'], ['⚠ Safety-critical gaps', SC, SC ? 'bad' : 'good']]
      .map(([k, v, c]) => `<div class="tile ${c || ''}"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
    const pct = (p, r) => r ? Math.round(p / r * 100) : 100;
    $('[data-rollup]', root).innerHTML = SYS.map((n, i) => [n, sysP[i], sysR[i]]).concat([['Whole vehicle', P, R, 1]]).map(([n, p, r, tot]) =>
      `<div class="barrow${tot ? ' total' : ''}"><span class="lab">${n}</span><span class="track"><i style="width:${pct(p, r)}%"></i></span><span class="val">${pct(p, r)}% · ${p}/${r}</span></div>`).join('');
  }
  $$('[data-mode]', root).forEach(b => b.onclick = () => { mode = b.dataset.mode; $$('[data-mode]', root).forEach(x => x.setAttribute('aria-pressed', x === b)); render(); });
  std.subs.push(render); render();
})();

/* §7.2 gap decision */
(() => {
  const ol = $('[data-decide]'); if (!ol) return;
  function render() {
    ol.innerHTML = `
      <li><span class="dot">?</span><div class="box q">“How is SRS-BRK-011 verified?”</div></li>
      <li><span class="dot">1</span><div class="box"><b>Document type:</b> “verified” is a testing word → a test description, <b>STD</b> (ontology §8).</div></li>
      <li><span class="dot">2</span><div class="box"><b>Subsystem:</b> SRS-BRK-011 is allocated to <code>SUB-BRK-ECU</code>.</div></li>
      <li><span class="dot">3</span><div class="box"><b>Baseline check:</b> does SUB-BRK-ECU have an STD? → <b>${std.on ? 'Yes: STD-BRK-001' : 'No'}</b></div></li>
      <li><span class="dot">→</span><div class="branch">
        <div class="br gapb ${std.on ? '' : 'on'}"><h4>Report the gap first</h4><p>“…the Brake ECU test description (STD) for subsystem SUB-BRK-ECU does not exist in the baseline yet… any detailed test sequence or thresholds would be an invention and must not be assumed.”</p></div>
        <div class="br okb ${std.on ? 'on' : ''}"><h4>Answer, with a citation</h4><p>“The requirement SRS-BRK-011 is verified by test case TC-BRK-011 … [STD-BRK-001] … every one of the 200 releases … completes within 15 ms.”</p></div>
      </div></li>`;
  }
  std.subs.push(render); render();
})();

/* §10 traceability */
(() => {
  const grid = $('[data-tgrid]'); if (!grid) return;
  const R = { 'Brake ECU|SUB-BRK-ECU · SRS-BRK-001': ['SRS-BRK-010', 'SRS-BRK-011', 'SRS-BRK-012', 'SRS-BRK-020', 'SRS-BRK-021', 'SRS-BRK-022', 'SRS-BRK-030', 'SRS-BRK-031', 'SRS-BRK-032'],
              'Driver cluster|SUB-DCL · SRS-DCL-001': ['SRS-DCL-010', 'SRS-DCL-011', 'SRS-DCL-020', 'SRS-DCL-021', 'SRS-DCL-022', 'SRS-DCL-030', 'SRS-DCL-031'] };
  const V = { 'SRS-BRK-010': 'TC-BRK-010', 'SRS-BRK-011': 'TC-BRK-011', 'SRS-BRK-021': 'TC-BRK-021', 'SRS-BRK-030': 'TC-BRK-030' };
  const det = $('[data-tdetail]'); let sel = null;
  function render() {
    let tot = 0, ver = 0;
    grid.innerHTML = Object.entries(R).map(([k, ids]) => { const [nm, sub] = k.split('|'); let v = 0;
      const chips = ids.map(id => { tot++; const ok = std.on && V[id]; if (ok) { v++; ver++; }
        return `<button class="req${ok ? ' ver' : ''}" aria-pressed="${id === sel}" data-id="${id}">${id}</button>`; }).join('');
      return `<div class="trow"><div class="nm">${nm}<small>${sub} · ${v} of ${ids.length} verified</small></div><div class="reqs">${chips}</div></div>`; }).join('');
    $('[data-tsum]').innerHTML = `<span style="color:var(${ver ? '--ok' : '--crit'})">${ver}</span> of ${tot} verified`;
    if (sel) show(sel);
  }
  function show(id) { sel = id; $$('.req', grid).forEach(b => b.setAttribute('aria-pressed', b.dataset.id === id));
    det.innerHTML = std.on && V[id] ? `<b>${id}</b> is verified by <code>${V[id]}</code> in <code>STD-BRK-001</code>.` : `<b>${id}</b>: no test case verifies this.`; }
  grid.addEventListener('click', e => { const b = e.target.closest('.req'); if (b) show(b.dataset.id); });
  std.subs.push(render); render();
})();

/* §11.1 SwCI */
(() => {
  const t = $('[data-swci]'); if (!t) return;
  const SEV = ['Catastrophic', 'Critical', 'Marginal', 'Negligible'];
  const SCC = [['1', 'Autonomous'], ['2', 'Semi-autonomous'], ['3', 'Redundant fault tolerant'], ['4', 'Influential'], ['5', 'No safety impact']];
  const M = [[1, 1, 3, 4], [1, 2, 3, 4], [2, 3, 4, 4], [3, 4, 4, 4], [5, 5, 5, 5]];
  const RIGOR = { 1: 'Program shall perform analysis of requirements, architecture, design, and code; and conduct in-depth safety-specific testing.',
    2: 'Program shall perform analysis of requirements, architecture, and design; and conduct in-depth safety-specific testing.',
    3: 'Program shall perform analysis of requirements and architecture; and conduct in-depth safety-specific testing.',
    4: 'Program shall conduct safety-specific testing.', 5: 'Once assessed by safety engineering as Not Safety, then no safety specific analysis or verification is required.' };
  const RISK = { 1: 'High', 2: 'Serious', 3: 'Medium', 4: 'Low', 5: 'Not Safety' };
  t.innerHTML = `<thead><tr><th>Control category</th>${SEV.map(s => `<th>${s}</th>`).join('')}</tr></thead><tbody>` +
    SCC.map(([n, nm], r) => `<tr><td>${n}<small>${nm}</small></td>${M[r].map((v, c) => `<td><button class="sw-cell l${v}" data-r="${r}" data-c="${c}" aria-pressed="false" aria-label="${SEV[c]}, category ${n}: SwCI ${v}">SwCI ${v}</button></td>`).join('')}</tr>`).join('') + '</tbody>';
  const out = $('[data-swci-out]');
  function pick(r, c) { const v = M[r][c];
    $$('.sw-cell', t).forEach(b => b.setAttribute('aria-pressed', +b.dataset.r === r && +b.dataset.c === c));
    out.innerHTML = `<div class="ctl-label">${SEV[c]} × category ${SCC[r][0]} (${SCC[r][1].toLowerCase()})</div><div class="res">SwCI ${v}</div>
      <p><b>Level of rigor:</b> ${RIGOR[v]}</p><p><b>If the rigor isn't met</b> (Table VI), the software's contribution to risk is documented as <b>${RISK[v]}</b>.</p>
      ${r === 1 && c === 1 ? '<p class="src">This is the example brief: SSF-ENG-01, Critical × category 2.</p>' : ''}<p class="src">MIL-STD-882E Tables V and VI, from ESAM's standards library.</p>`; }
  t.addEventListener('click', e => { const b = e.target.closest('.sw-cell'); if (b) pick(+b.dataset.r, +b.dataset.c); });
  pick(1, 1);
})();

/* §12.4 review pipeline */
(() => {
  const ol = $('[data-pipe]'); if (!ol) return;
  const S = [['📥', 'Inbox', 'you', 'Put <code>STD-BRK-001.md</code> in <code>work/inbox/</code> with its header table, then queue <i>Review a document for ingestion</i>. The file name must be the document id.'],
    ['🧭', 'Triage', 'agent', 'Checks the header against the ontology: the right document type for that subsystem, a real subsystem, a valid id and revision.'],
    ['🔎', 'Researcher', 'agent', 'Reads the content. For a test description, compares every pass criterion with the requirement it verifies: <b>matches</b>, <b>stricter</b>, <b>weaker</b> or <b>unclear</b>, quoting both.'],
    ['📝', 'SE Reviewer', 'agent', 'Writes the <b>approval request</b>, ending with <i>RECOMMENDATION: INGEST</i> or <i>DO NOT INGEST</i>. The agents stop here.'],
    ['🧑‍💼', 'You decide', 'human', '<b>Approve: ingest + verify</b>, or <b>Reject</b> with a note. Approving against a DO NOT INGEST needs the override box and a reason. An ontology error can\'t be approved past.'],
    ['✅', 'Ingest + verify', 'auto', 'Adds the document to the baseline and search store, reloads the graph, then checks it is really in: chunks stored, attached, links present, searchable, integrity passing. Result: <i>verified</i> or <i>verification failed</i>, plus findings closed.']];
  ol.innerHTML = S.map((s, i) => `<li class="${s[2]}"><button aria-pressed="${i === 0}" data-i="${i}"><span class="ic">${s[0]}</span><b>${s[1]}</b><small>${s[2] === 'agent' ? 'reads only through ESAM checks' : s[2] === 'human' ? 'recorded under your name' : s[2] === 'you' ? 'you' : 'ESAM'}</small></button></li>`).join('');
  const d = $('[data-pipe-detail]'); const show = i => { d.innerHTML = `<b>${S[i][1]}.</b> ${S[i][3]}`; $$('button', ol).forEach((b, j) => b.setAttribute('aria-pressed', j === i)); };
  ol.addEventListener('click', e => { const b = e.target.closest('button'); if (b) show(+b.dataset.i); }); show(0);
})();

/* §12.9 cost */
(() => {
  const box = $('[data-cost]'); if (!box) return;
  const T = [['Check the environment', 15, '~15 s', 3, '~3k'], ['Review a document', 60, '~1 min', 70, '~70k'], ['Approve (ingest + verify)', 40, '~40 s', 13, '~13k'],
    ['Ask a question', 180, 'a few min', 80, '~80k'], ['Draft an SRS', 300, '~5 min', 500, '~500k']];
  function render(m) { const i = m === 'time' ? 1 : 3, max = Math.max(...T.map(r => r[i]));
    box.innerHTML = T.map(r => `<div class="barrow"><span class="lab">${r[0]}</span><span class="track"><i style="width:0" data-w="${r[i] / max * 100}"></i></span><span class="val">${r[i + 1]}</span></div>`).join('');
    requestAnimationFrame(() => requestAnimationFrame(() => $$('i[data-w]', box).forEach(x => x.style.width = Math.max(1, +x.dataset.w) + '%'))); }
  $$('[data-cm]').forEach(b => b.onclick = () => { $$('[data-cm]').forEach(x => x.setAttribute('aria-pressed', x === b)); render(b.dataset.cm); });
  render('tokens');
})();
})();
"""


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(HERE.parent)} ({OUT.stat().st_size // 1024} KB)")
