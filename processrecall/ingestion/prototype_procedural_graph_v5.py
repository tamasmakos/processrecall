# ruff: noqa: D101, D102, D103 -- throwaway prototype
"""PROTOTYPE v5 -- throwaway, not production. Builds on v4 (granularity and recall).

Question: six things v4 left untried, each answered with a number on the same held-out split.

  E1 artifact-conditioned recall   -- does "the current step touches the same file as the
                                      step before" predict the next node better than the
                                      node alone?
  E2 success-weighted edges        -- weight training transitions by whether their prompt
                                      ended clean; score on all held-out prompts and on
                                      clean ones only.
  E3 variable-order sequence model -- back-off n-gram (orders 3 -> 2 -> 1) over node
                                      sequences vs the bigram.
  E4 tighter templates             -- a redirect grammar so `2>/dev/null` stops leaking
                                      into action templates as `<N> > <Dir>`.
  E5 transfer to another repo      -- coverage of the program table on a different
                                      project's session, and next-step recall trained here
                                      and tested there.
  E6 recall latency                -- in-memory next-step lookup, cold JSON load included.

No model in the loop: v5 runs the rules only, so GLiNER2's 4% Unknown fallback is absent.

Run from the repo root:
    python -m graphknows.ingestion.prototype_procedural_graph_v5 [--sessions N]
"""

from __future__ import annotations

import html
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graphknows.ingestion.prototype_procedural_graph import (
    CACHE,
    PATHLIKE,
    classify_outcome,
    steps_from,
)
from graphknows.ingestion.prototype_procedural_graph_v4 import (
    LEVELS,
    is_test,
    node_at,
    project_sessions,
    subs_of,
)

OUT_DIR = Path(os.environ.get("PG_PROTOTYPE_OUT", CACHE))
OTHER_PROJECT = Path.home() / ".claude" / "projects" / "C--Users-User-Documents-dev-the-line"

# ------------------------------------------------------------------ sequences


@dataclass
class Ev:
    nodes: tuple[str, str, str]
    outcome: str
    files: frozenset[str]


@dataclass
class Seq:
    key: tuple[str, str]
    events: list[Ev] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return bool(self.events) and self.events[-1].outcome == "ok"


def sequences(paths: list[Path], limit: int | None = None) -> tuple[list[Seq], dict[str, Any]]:
    seqs: dict[tuple[str, str], Seq] = {}
    unknown: Counter[str] = Counter()
    total = full = 0
    for path in paths:
        steps = steps_from(path, limit)
        for step in steps:
            subs = subs_of(step)
            step.activities = [(x.cls, x.program) for x in subs]
            step.outcome = classify_outcome(step)
            total += 1
            if all(x.cls != "Unknown Activity" for x in subs):
                full += 1
            else:
                unknown.update(x.program for x in subs if x.cls == "Unknown Activity")
            if not step.prompt_id:
                continue
            key = (path.stem[:8], step.prompt_id)
            files = frozenset(p for x in subs for _, p in x.artifacts if Path(p).suffix)
            seqs.setdefault(key, Seq(key)).events.append(
                Ev((node_at(subs, 0), node_at(subs, 1), node_at(subs, 2)), step.outcome, files)
            )
    meta = {
        "sessions": len(paths),
        "steps": total,
        "fully classified": f"{full} / {total} ({full / max(1, total):.0%})",
        "unknown programs": dict(unknown.most_common(8)),
    }
    return list(seqs.values()), meta


# ----------------------------------------------------------------- predictors


@dataclass
class Sample:
    ctx: tuple[str, ...]
    target: str
    weight: float
    success: bool
    same_file: bool
    history: tuple[str, ...]  # nodes before the current one, most recent last


def samples(seqs: list[Seq], lv: int) -> list[Sample]:
    out: list[Sample] = []
    for seq in seqs:
        ev = seq.events
        for i in range(len(ev) - 1):
            same = bool(ev[i].files & ev[i - 1].files) if i else False
            out.append(
                Sample(
                    (ev[i].nodes[lv],),
                    ev[i + 1].nodes[lv],
                    1.0,
                    seq.success,
                    same,
                    tuple(e.nodes[lv] for e in ev[max(0, i - 2) : i]),
                )
            )
    return out


def score(
    train: list[Sample], test: list[Sample], ctx_of: Any, weight_of: Any = None
) -> tuple[float, float]:
    table: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    majority: Counter[str] = Counter()
    for s in train:
        w: int = int(weight_of(s)) if weight_of else 1
        table[ctx_of(s)][s.target] += w
        majority[s.target] += w
    fallback = [n for n, _ in majority.most_common(3)]
    top1: float = 0.0
    top3: float = 0.0
    for s in test:
        ranked = (
            [n for n, _ in table[ctx_of(s)].most_common(3)] if table.get(ctx_of(s)) else fallback
        )
        top1 += ranked[:1] == [s.target]
        top3 += s.target in ranked
    n = max(1, len(test))
    return top1 / n, top3 / n


def backoff_score(
    train: list[Sample], test: list[Sample], max_order: int, min_count: int = 2
) -> tuple[float, float]:
    """Predict from the longest history context seen at least ``min_count`` times."""
    tables: list[dict[tuple[str, ...], Counter[str]]] = [
        defaultdict(Counter) for _ in range(max_order)
    ]
    for s in train:
        for order in range(max_order):
            ctx = (*s.history[len(s.history) - order :], *s.ctx) if order else s.ctx
            if order and len(s.history) < order:
                continue
            tables[order][ctx][s.target] += 1
    majority = [n for n, _ in Counter(s.target for s in train).most_common(3)]
    top1: float = 0.0
    top3: float = 0.0
    for s in test:
        ranked = majority
        for order in reversed(range(max_order)):
            if order and len(s.history) < order:
                continue
            ctx = (*s.history[len(s.history) - order :], *s.ctx) if order else s.ctx
            hits = tables[order].get(ctx)
            if hits and sum(hits.values()) >= min_count:
                ranked = [n for n, _ in hits.most_common(3)]
                break
        top1 += ranked[:1] == [s.target]
        top3 += s.target in ranked
    n = max(1, len(test))
    return top1 / n, top3 / n


def experiments(seqs: list[Seq]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # split by prompt, so a held-out prompt is unseen end to end
    train_keys = {seq.key for seq in seqs if not is_test(seq.key[1])}
    for lv, level in enumerate(LEVELS):
        train_s = samples([q for q in seqs if q.key in train_keys], lv)
        test_s = samples([q for q in seqs if q.key not in train_keys], lv)
        test_ok = [s for s in test_s if s.success]
        base = score(train_s, test_s, lambda s: ("<any>",))
        bigram = score(train_s, test_s, lambda s: s.ctx)
        e1 = score(train_s, test_s, lambda s: (*s.ctx, "same" if s.same_file else "other"))
        e1_locality = sum(1 for s in test_s if s.same_file) / max(1, len(test_s))
        e2_plain_ok = score(train_s, test_ok, lambda s: s.ctx)
        e2_weighted_all = score(train_s, test_s, lambda s: s.ctx, lambda s: 4 if s.success else 1)
        e2_weighted_ok = score(train_s, test_ok, lambda s: s.ctx, lambda s: 4 if s.success else 1)
        e2_only_ok = score([s for s in train_s if s.success], test_ok, lambda s: s.ctx)
        e3_2 = backoff_score(train_s, test_s, 2)
        e3_3 = backoff_score(train_s, test_s, 3)
        rows.append({
            "granularity": level, "train": len(train_s), "test": len(test_s), "test clean": len(test_ok),
            "baseline": base, "bigram": bigram,
            "E1 same-file ctx": e1, "E1 share of steps on same file as previous": round(e1_locality, 2),
            "E2 bigram on clean prompts": e2_plain_ok, "E2 weighted, all": e2_weighted_all, "E2 weighted, clean": e2_weighted_ok, "E2 clean-only training, clean": e2_only_ok,
            "E3 backoff order 2": e3_2, "E3 backoff order 3": e3_3,
        })  # fmt: skip
    return rows


# ------------------------------------------------------------------ templates (E4)

REDIRECT_OPS = {">", ">>", ">&", "<", "<&"}


def template_v4(tokens: list[str]) -> str:
    out = []
    for t in tokens[:12]:
        if PATHLIKE.match(t):
            out.append("<Dir>" if not Path(t).suffix else "<File>")
        elif t.isdigit():
            out.append("<N>")
        elif " " in t or len(t) > 40:
            out.append("<str>")
        else:
            out.append(t)
    return " ".join(out)


def template_v5(tokens: list[str]) -> str:
    """Redirects (`2>/dev/null`, `>&1`, `> out.txt`) fold to one `<redirect>` token."""
    out: list[str] = []
    i = 0
    toks = tokens[:16]
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t in REDIRECT_OPS or (t.isdigit() and nxt in REDIRECT_OPS):
            skip = 2 if t.isdigit() else 1  # fd, op
            skip += 1 if i + skip < len(toks) else 0  # target
            i += skip
            if not out or out[-1] != "<redirect>":
                out.append("<redirect>")
            continue
        if PATHLIKE.match(t):
            out.append("<Dir>" if not Path(t).suffix else "<File>")
        elif t.isdigit():
            out.append("<N>")
        elif " " in t or len(t) > 40:
            out.append("<str>")
        else:
            out.append(t)
        i += 1
    return " ".join(out[:12])


def template_experiment(paths: list[Path]) -> dict[str, Any]:
    per4: dict[str, Counter[str]] = defaultdict(Counter)
    per5: dict[str, Counter[str]] = defaultdict(Counter)
    leaks4 = leaks5 = 0
    for path in paths:
        for step in steps_from(path, None):
            for sub in subs_of(step):
                node = f"{sub.cls}/{sub.program}"
                t4, t5 = template_v4(sub.tokens), template_v5(sub.tokens)
                per4[node][t4] += 1
                per5[node][t5] += 1
                leaks4 += "<N> >" in t4 or "> <Dir>" in t4
                leaks5 += "<N> >" in t5 or "> <Dir>" in t5
    top = sorted(per5, key=lambda n: -sum(per5[n].values()))[:10]
    return {
        "distinct templates v4 / v5": f"{sum(len(c) for c in per4.values())} / {sum(len(c) for c in per5.values())}",
        "redirect leaks v4 / v5": f"{leaks4} / {leaks5}",
        "top nodes": {n: [f"{t} x{k}" for t, k in per5[n].most_common(3)] for n in top},
    }


# ---------------------------------------------------------------- transfer (E5)


def transfer(home_seqs: list[Seq]) -> dict[str, Any]:
    other_paths = sorted(OTHER_PROJECT.glob("*.jsonl"), key=lambda p: -p.stat().st_size)[:3]
    if not other_paths:
        return {"other project": "none found"}
    other_seqs, meta = sequences(other_paths)
    out: dict[str, Any] = {"other project": OTHER_PROJECT.name, **meta}
    for lv, level in enumerate(LEVELS):
        home = samples(home_seqs, lv)
        other = samples(other_seqs, lv)
        keys = {q.key for q in other_seqs if not is_test(q.key[1])}
        other_train = samples([q for q in other_seqs if q.key in keys], lv)
        other_test = samples([q for q in other_seqs if q.key not in keys], lv)
        out[level] = {
            "other transitions": len(other),
            "majority baseline on other": score(home, other, lambda s: ("<any>",)),
            "trained here, tested there": score(home, other, lambda s: s.ctx),
            "trained there (75%), tested there": score(other_train, other_test, lambda s: s.ctx)
            if other_test
            else "n/a",
            "trained here + there": score(home + other_train, other_test, lambda s: s.ctx)
            if other_test
            else "n/a",
        }
    return out


# ------------------------------------------------------------------ latency (E6)


def latency(seqs: list[Seq]) -> dict[str, Any]:
    graph: dict[str, list[tuple[str, int]]] = {}
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for seq in seqs:
        for a, b in zip(seq.events, seq.events[1:], strict=False):
            counts[a.nodes[2]][b.nodes[2]] += 1
    graph = {k: v.most_common(5) for k, v in counts.items()}
    path = OUT_DIR / "procedural_v5_graph.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph), encoding="utf-8")
    t = time.perf_counter()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    cold_ms = (time.perf_counter() - t) * 1000
    keys = list(loaded)
    times = []
    for i in range(20000):
        k = keys[i % len(keys)]
        t = time.perf_counter_ns()
        _ = loaded.get(k, [])[:3]
        times.append(time.perf_counter_ns() - t)
    times.sort()
    return {
        "nodes": len(loaded),
        "file bytes": path.stat().st_size,
        "cold load ms": round(cold_ms, 2),
        "lookup p50 us": round(statistics.median(times) / 1000, 2),
        "lookup p99 us": round(times[int(len(times) * 0.99)] / 1000, 2),
        "lookups measured": len(times),
    }


# ------------------------------------------------------------------ reporting


def _pct(v: Any) -> str:
    return f"{v[0]:.0%} / {v[1]:.0%}" if isinstance(v, tuple) else html.escape(str(v))


def report_html(
    meta: dict[str, Any],
    rows: list[dict[str, Any]],
    tmpl: dict[str, Any],
    trans: dict[str, Any],
    lat: dict[str, Any],
) -> str:
    esc = html.escape
    cols = [k for k in rows[0] if k not in ("granularity", "train", "test", "test clean")]
    head = (
        "<tr><th>metric (top-1 / top-3)</th>"
        + "".join(
            f"<th>{esc(r['granularity'])}<br><small>{r['train']} train / {r['test']} test / {r['test clean']} clean</small></th>"
            for r in rows
        )
        + "</tr>"
    )
    body = "".join(
        f"<tr><td>{esc(c)}</td>" + "".join(f"<td>{_pct(r[c])}</td>" for r in rows) + "</tr>"
        for c in cols
    )
    tmpl_rows = "".join(
        f"<tr><td><code>{esc(n)}</code></td><td>{'<br>'.join(f'<code>{esc(t)}</code>' for t in ts)}</td></tr>"
        for n, ts in tmpl["top nodes"].items()
    )
    trans_rows = "".join(
        f"<tr><td>{esc(level)}</td>"
        + "".join(f"<td>{_pct(v)}</td>" for v in trans[level].values())
        + "</tr>"
        for level in LEVELS
        if level in trans
    )
    trans_head = (
        "<tr><th>granularity</th>"
        + "".join(f"<th>{esc(k)}</th>" for k in (trans.get(LEVELS[0]) or {}))
        + "</tr>"
    )
    lines = "".join(
        f"<li><b>{esc(k)}</b>: {esc(json.dumps(v) if isinstance(v, dict) else str(v))}</li>"
        for k, v in {**meta, **{k: v for k, v in trans.items() if k not in LEVELS}}.items()
    )
    lat_lines = "".join(f"<li><b>{esc(k)}</b>: {esc(str(v))}</li>" for k, v in lat.items())
    question = (__doc__ or "").split("\n\n")[1]
    return f"""<title>Procedural graph v5: six experiments</title>
<style>body{{font:14px system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#222}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}td,th{{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top;font-size:12px}}
th{{background:#f3f3f3}}code{{background:#f4f0ff;padding:0 3px}}h2{{border-bottom:2px solid #6b4fd8;padding-bottom:4px;margin-top:2rem}}
.q{{background:#f4f0ff;padding:1rem;border-left:4px solid #6b4fd8;white-space:pre-wrap}}</style>
<h1>Six things left to try (v5)</h1>
<p class=q>{esc(question)}</p>
<h2>Corpus (rules only, no model)</h2><ul>{lines}</ul>
<h2>E1 E2 E3: held-out next-step recall</h2>
<table>{head}{body}</table>
<h2>E4: templates with a redirect grammar</h2>
<ul><li><b>distinct templates v4 / v5</b>: {esc(tmpl["distinct templates v4 / v5"])}</li><li><b>redirect leaks v4 / v5</b>: {esc(tmpl["redirect leaks v4 / v5"])}</li></ul>
<table><tr><th>node</th><th>top templates (v5)</th></tr>{tmpl_rows}</table>
<h2>E5: transfer to {esc(str(trans.get("other project", "")))}</h2>
<table>{trans_head}{trans_rows}</table>
<h2>E6: recall latency (in-memory, class/program/ext graph)</h2><ul>{lat_lines}</ul>
"""


def main(argv: list[str]) -> None:
    n = int(argv[argv.index("--sessions") + 1]) if "--sessions" in argv else 12
    t0 = time.perf_counter()
    paths = project_sessions(n)
    seqs, meta = sequences(paths)
    meta["prompts (sequences)"] = len(seqs)
    meta["clean prompts"] = sum(q.success for q in seqs)
    rows = experiments(seqs)
    tmpl = template_experiment(paths)
    trans = transfer(seqs)
    lat = latency(seqs)
    meta["seconds, whole pipeline"] = round(time.perf_counter() - t0)
    for k, v in meta.items():
        print(f"{k:32s} {v}")
    for r in rows:
        print(
            f"\n== {r['granularity']}  (train {r['train']}, test {r['test']}, clean test {r['test clean']})"
        )
        for k, v in r.items():
            if isinstance(v, tuple):
                print(f"  {k:36s} top1={v[0]:.0%} top3={v[1]:.0%}")
            elif k not in ("granularity", "train", "test", "test clean"):
                print(f"  {k:36s} {v}")
    print(
        "\nE4 templates:",
        tmpl["distinct templates v4 / v5"],
        "| leaks",
        tmpl["redirect leaks v4 / v5"],
    )
    print("E5 transfer:", json.dumps({k: v for k, v in trans.items()}, default=str)[:900])
    print("E6 latency:", lat)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "procedural_v5.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "experiments": rows,
                "templates": tmpl,
                "transfer": trans,
                "latency": lat,
            },
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    (OUT_DIR / "procedural_v5.html").write_text(
        report_html(meta, rows, tmpl, trans, lat), encoding="utf-8"
    )
    print(f"\nwrote {OUT_DIR / 'procedural_v5'}.{{json,html}}")


if __name__ == "__main__":
    main(sys.argv[1:])
