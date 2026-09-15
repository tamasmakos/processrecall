# ruff: noqa: D101, D102, D103, C901 -- throwaway prototype
"""PROTOTYPE v4 -- throwaway, not production. Builds on v3 (prototype_procedural_graph).

Question: v3 gives consistent triplets; are they *useful*? Which procedure granularity is
worth storing, and what conditioning makes next-step recall beat a majority baseline?

Adds to v3:
  1. Composite steps: one Bash call -> ordered sub-activities (SPO composition), each with
     its own class, program, template and artifacts.
  2. Artifact identity by normalized repo-relative path (container /app and absolute paths
     folded), so `__init__.py` no longer collides across packages.
  3. Three procedure granularities side by side: class | class/program | class/program/ext.
  4. The user prompt typed as a process (se-on.org BugFix / FeatureAddition / Enhancement,
     ACE Investigation / Documentation) by GLiNER2, so edges can be conditioned on it.
  5. Action templates per node: the command shape with paths and numbers abstracted --
     structured guidance, no prose.
  6. A multi-session corpus and a held-out next-step evaluation (top-1 / top-3 accuracy vs
     the majority baseline) per granularity and conditioning. Plus the edit->verify rate:
     how often a changed file is evaluated within the next five steps of the same prompt.

Run from the repo root:
    python -m graphknows.ingestion.prototype_procedural_graph_v4 [--sessions N] [--limit N]
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from graphknows.ingestion.prototype_procedural_graph import (
    CACHE,
    PATHLIKE,
    PRIORITY,
    TOOL_ACTIVITY,
    Step,
    artifacts_in,
    check_consistency,
    classify_outcome,
    classify_program,
    evaluation_report,
    gliner_model,
    load_ontologies,
    run_gliner,
    steps_from,
    subcommands,
)

OUT_DIR = Path(os.environ.get("PG_PROTOTYPE_OUT", CACHE))
COMMIT_HASH = re.compile(r"\[[\w./-]+ (?:\(root-commit\) )?([0-9a-f]{7,})\]")

# --------------------------------------------------------------- sub-activities


@dataclass
class Sub:
    cls: str
    program: str
    tokens: list[str]
    artifacts: list[tuple[str, str]] = field(default_factory=list)  # (verb, normalized path)

    @property
    def template(self) -> str:
        out = []
        for t in self.tokens[:12]:
            if PATHLIKE.match(t):
                out.append("<Dir>" if not Path(t).suffix else "<File>")
            elif t.isdigit():
                out.append("<N>")
            elif " " in t or len(t) > 40:
                out.append("<str>")
            else:
                out.append(t)
        return " ".join(out)


def norm_path(path: str, cwd: str) -> str:
    """Repo-relative identity for a path: container mount and absolute prefixes folded."""
    p = path.replace("\\", "/")
    c = cwd.replace("\\", "/").rstrip("/")
    if p.startswith("/app/"):
        p = p[5:]
    elif c and p.lower().startswith(c.lower() + "/"):
        p = p[len(c) + 1 :]
    elif re.match(r"^(?:[A-Za-z]:)?/", p) and "/graphknows/" in p:
        p = p.split("/graphknows/", 1)[1]  # absolute path anchored at the repo name
    return p.lstrip("./") or path


VERB_BY_TOOL = {"Edit": "changes", "NotebookEdit": "changes", "Write": "creates"}


def subs_of(step: Step) -> list[Sub]:
    payload = step.payload
    subs: list[Sub] = []
    if step.tool in ("Bash", "PowerShell"):
        for tokens in subcommands(str(payload.get("command", ""))):
            cls, program = classify_program(tokens)
            if not cls:
                continue
            arts = [(verb, norm_path(p, step.cwd)) for verb, p in artifacts_in(tokens)]
            subs.append(Sub(cls, program, tokens, arts))
    else:
        cls = TOOL_ACTIVITY.get(step.tool, "Unknown Activity")
        path = next(
            (
                str(payload[k])
                for k in ("file_path", "notebook_path", "path")
                if isinstance(payload.get(k), str) and payload[k]
            ),
            "",
        )
        arts = [(VERB_BY_TOOL.get(step.tool, "uses"), norm_path(path, step.cwd))] if path else []
        subs.append(Sub(cls, step.tool, [step.tool, *sorted(payload)], arts))
    if not subs:
        subs.append(Sub("Unknown Activity", step.tool, [step.tool]))
    return subs


def primary(subs: list[Sub]) -> Sub:
    return min(subs, key=lambda s: PRIORITY.index(s.cls))


def node_at(subs: list[Sub], level: int) -> str:
    p = primary(subs)
    if level == 0:
        return p.cls
    if level == 1:
        return f"{p.cls}/{p.program}"
    ext = next((Path(path).suffix or "<dir>" for _, path in p.artifacts), "-")
    return f"{p.cls}/{p.program}/{ext}"


LEVELS = ["class", "class/program", "class/program/ext"]

# ---------------------------------------------------------------- prompt typing

# Definitions quoted from se-on.org issues.owl where the class exists there; ACE otherwise.
PROCESS_LABELS = {
    "BugFix": "The act of fixing a bug.",
    "FeatureAddition": "The act of adding a new feature to a program.",
    "Enhancement": "The act of enhancing an existing program.",
    "Investigation": "understanding, diagnosing, reviewing or explaining without changing code",
    "Documentation": "writing or updating documentation, specifications or plans",
    "Release Management and Delivery": "committing, merging, releasing, branching or repository housekeeping",
}


def prompt_texts(path: Path) -> dict[str, str]:
    """Map promptId -> the user's own text for that prompt (first 600 chars)."""
    out: dict[str, str] = {}
    for line in path.read_bytes().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") != "user" or record.get("isMeta"):
            continue
        pid = str(record.get("promptId") or "")
        if not pid or pid in out:
            continue
        content = (record.get("message") or {}).get("content")
        text = (
            content
            if isinstance(content, str)
            else " ".join(
                b.get("text", "")
                for b in content or []
                if isinstance(b, dict) and b.get("type") == "text"
            )
        )
        if text.strip():
            out[pid] = text.strip()[:600]
    return out


AUTOMATED = re.compile(
    r"<task-notification>|<system-reminder>|<command-name>|\[SYSTEM NOTIFICATION", re.I
)
TAGS = re.compile(r"<[^>]{1,60}>")


def type_prompts(texts: dict[str, str]) -> dict[str, tuple[str, float]]:
    """Classify each prompt; harness-generated prompts are typed Automated without a model call."""
    typed: dict[str, tuple[str, float]] = {}
    human: dict[str, str] = {}
    for pid, text in texts.items():
        if AUTOMATED.search(text):
            typed[pid] = ("Automated", 1.0)
        else:
            clean = TAGS.sub(" ", text).strip()
            if len(clean) >= 12:
                human[pid] = clean
            else:
                typed[pid] = ("Automated", 1.0)
    if not human:
        return typed
    model = gliner_model()
    task = {
        "process": {
            "labels": [f"{k}: {v}" for k, v in PROCESS_LABELS.items()],
            "multi_label": False,
        }
    }
    ids = list(human)
    outs = model.batch_classify_text(
        [human[i] for i in ids], task, batch_size=16, include_confidence=True
    )
    for pid, out in zip(ids, outs, strict=True):
        hit = (out or {}).get("process") or {}
        typed[pid] = (
            str(hit.get("label", "")).split(":", 1)[0],
            round(float(hit.get("confidence", 0)), 2),
        )
    return typed


# -------------------------------------------------------------------- triplets


def map_corpus(
    sessions: list[tuple[str, list[Step], dict[str, list[Sub]]]],
    processes: dict[str, tuple[str, float]],
) -> list[dict[str, Any]]:
    tr: list[dict[str, Any]] = []

    def emit(s: str, st: str, p: str, o: str, ot: str, step: Step) -> None:
        tr.append(
            {
                "s": s,
                "st": st,
                "p": p,
                "o": o,
                "ot": ot,
                "step": step.id,
                "provenance": step.tool_use_id,
            }
        )

    for sid, steps, subs_by in sessions:
        seen_prompts: set[str] = set()
        for step in steps:
            subs = subs_by[step.id]
            cls = primary(subs).cls
            for index, sub in enumerate(subs):
                sub_id = f"{step.id}/{index}" if len(subs) > 1 else step.id
                if len(subs) > 1:
                    emit(step.id, cls, "composed of", sub_id, sub.cls, step)
                    if index:
                        emit(
                            sub_id,
                            sub.cls,
                            "depends on",
                            f"{step.id}/{index - 1}",
                            subs[index - 1].cls,
                            step,
                        )
                emit(
                    sub_id,
                    sub.cls,
                    "uses",
                    f"Software Resource:{sub.program}",
                    "Software Resource",
                    step,
                )
                for verb, path in sub.artifacts:
                    kind = "Directory" if not Path(path).suffix else "File"
                    emit(sub_id, sub.cls, verb, f"{kind}:{path}", kind, step)
            step.outcome = classify_outcome(step)
            emit(
                step.id,
                cls,
                "has outcome",
                f"Information Item:outcome={step.outcome}",
                "Information Item",
                step,
            )
            if cls == "Artifact Evaluation":
                summary, noncompliant = evaluation_report(step)
                emit(
                    step.id,
                    cls,
                    "creates",
                    f"Evaluation Report:{step.id} {summary}",
                    "Evaluation Report",
                    step,
                )
                if noncompliant:
                    emit(
                        f"{step.id}/nc",
                        "Noncompliance Identification",
                        "creates",
                        f"Noncompliance Register:{step.result_text[:120]}",
                        "Noncompliance Register",
                        step,
                    )
                    emit(
                        f"{step.id}/nc",
                        "Noncompliance Identification",
                        "depends on",
                        step.id,
                        cls,
                        step,
                    )
            if cls == "Checkin" and (m := COMMIT_HASH.search(step.result_text)):
                emit(step.id, cls, "creates", f"Version:{m.group(1)}", "Version", step)
            if step.model:
                emit(
                    f"Stakeholder:{step.model}{'/subagent' if step.sidechain else ''}",
                    "Stakeholder",
                    "is in charge of",
                    step.id,
                    cls,
                    step,
                )
            if step.prompt_id:
                pid = f"Prompt:{sid}/{step.prompt_id[:8]}"
                emit(pid, "Prompt", "composed of", step.id, cls, step)
                if step.prompt_id not in seen_prompts and step.prompt_id in processes:
                    seen_prompts.add(step.prompt_id)
                    emit(
                        pid,
                        "Prompt",
                        "caused by",
                        f"Intended Process:{processes[step.prompt_id][0]}",
                        "Intended Process",
                        step,
                    )
            if step.intended:
                emit(
                    step.id,
                    cls,
                    "caused by",
                    f"Intended Activity:{step.intended}",
                    "Intended Activity",
                    step,
                )
            if step.started and step.ended:
                emit(
                    step.id,
                    cls,
                    "framed by",
                    f"Time Interval:{step.started.isoformat()}/{step.duration_ms}ms",
                    "Time Interval",
                    step,
                )
        for prev, nxt in pairwise(steps):
            if prev.prompt_id and prev.prompt_id == nxt.prompt_id:
                emit(
                    nxt.id,
                    primary(subs_by[nxt.id]).cls,
                    "depends on",
                    prev.id,
                    primary(subs_by[prev.id]).cls,
                    nxt,
                )
    return tr


# ------------------------------------------------------------------ evaluation


@dataclass
class Transition:
    session: str
    prompt: str
    process: str
    prev_outcome: str
    nodes: tuple[str, str, str]  # current node at each level
    nexts: tuple[str, str, str]


def transitions(
    sessions: list[tuple[str, list[Step], dict[str, list[Sub]]]],
    processes: dict[str, tuple[str, float]],
) -> list[Transition]:
    out: list[Transition] = []
    for sid, steps, subs_by in sessions:
        for prev, nxt in pairwise(steps):
            if not prev.prompt_id or prev.prompt_id != nxt.prompt_id:
                continue
            out.append(
                Transition(
                    sid,
                    prev.prompt_id,
                    processes.get(prev.prompt_id, ("-", 0))[0],
                    prev.outcome,
                    tuple(node_at(subs_by[prev.id], lv) for lv in range(3)),  # type: ignore[arg-type]
                    tuple(node_at(subs_by[nxt.id], lv) for lv in range(3)),  # type: ignore[arg-type]
                )
            )
    return out


def is_test(prompt: str) -> bool:
    return (
        hashlib.sha1(prompt.encode(), usedforsecurity=False).hexdigest()[0] in "0123"
    )  # ~25% of prompts held out


CONDITIONINGS: dict[str, Callable[[Transition, int], tuple[str, ...]]] = {
    "node": lambda t, lv: (t.nodes[lv],),
    "node+outcome": lambda t, lv: (t.nodes[lv], "ok" if t.prev_outcome == "ok" else "not ok"),
    "node+process": lambda t, lv: (t.nodes[lv], t.process),
    "node+prev2": lambda t, lv: (t.nodes[lv], t.prev2[lv]),  # type: ignore[attr-defined]
}


def evaluate(trans: list[Transition]) -> list[dict[str, Any]]:
    """Held-out next-step accuracy per granularity and conditioning, against the majority baseline."""
    # second-order context: the node before the current one, within the same prompt
    by_prompt: dict[tuple[str, str], list[Transition]] = defaultdict(list)
    for t in trans:
        by_prompt[(t.session, t.prompt)].append(t)
    for seq in by_prompt.values():
        for i, t in enumerate(seq):
            t.prev2 = seq[i - 1].nodes if i else ("<start>",) * 3  # type: ignore[attr-defined]
    train = [t for t in trans if not is_test(t.prompt)]
    test = [t for t in trans if is_test(t.prompt)]
    rows = []
    for lv, level in enumerate(LEVELS):
        majority = Counter(t.nexts[lv] for t in train).most_common(3)
        base_top1 = sum(t.nexts[lv] == majority[0][0] for t in test) / max(1, len(test))
        base_top3 = sum(t.nexts[lv] in {m for m, _ in majority} for t in test) / max(1, len(test))
        nodes = {t.nodes[lv] for t in train} | {t.nexts[lv] for t in train}
        edges = {(t.nodes[lv], t.nexts[lv]) for t in train}
        for name, ctx in CONDITIONINGS.items():
            table: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
            for t in train:
                table[ctx(t, lv)][t.nexts[lv]] += 1
            top1 = top3 = 0
            for t in test:
                ranked = [
                    n for n, _ in (table.get(ctx(t, lv)) or Counter(dict(majority))).most_common(3)
                ]
                top1 += ranked[:1] == [t.nexts[lv]]
                top3 += t.nexts[lv] in ranked
            rows.append({
                "granularity": level, "conditioning": name, "nodes": len(nodes), "edges": len(edges), "contexts": len(table),
                "top1": round(top1 / max(1, len(test)), 3), "top3": round(top3 / max(1, len(test)), 3),
                "baseline top1": round(base_top1, 3), "baseline top3": round(base_top3, 3),
                "train / test transitions": f"{len(train)} / {len(test)}",
            })  # fmt: skip
    return rows


def edit_verify_rate(
    sessions: list[tuple[str, list[Step], dict[str, list[Sub]]]],
) -> dict[str, Any]:
    """After a Change Implementation on file F, is there an Artifact Evaluation within 5 steps of the same prompt?"""
    edits = verified = 0
    by_ext: Counter[str] = Counter()
    by_ext_ok: Counter[str] = Counter()
    for _, steps, subs_by in sessions:
        for i, step in enumerate(steps):
            subs = subs_by[step.id]
            changed = [p for s in subs for v, p in s.artifacts if v in ("changes", "creates")]
            if primary(subs).cls != "Change Implementation" or not changed:
                continue
            edits += 1
            ext = Path(changed[0]).suffix or "-"
            by_ext[ext] += 1
            window = steps[i + 1 : i + 6]
            if any(
                s.prompt_id == step.prompt_id
                and primary(subs_by[s.id]).cls == "Artifact Evaluation"
                for s in window
            ):
                verified += 1
                by_ext_ok[ext] += 1
    return {
        "edits with a file": edits,
        "verified within 5 steps": f"{verified} ({verified / max(1, edits):.0%})",
        "by extension": {e: f"{by_ext_ok[e]}/{n}" for e, n in by_ext.most_common(6)},
    }


def artifact_chains(
    sessions: list[tuple[str, list[Step], dict[str, list[Sub]]]], top: int = 8
) -> list[tuple[str, int, str]]:
    touches: dict[str, list[str]] = defaultdict(list)
    for _, steps, subs_by in sessions:
        for step in steps:
            for sub in subs_by[step.id]:
                for verb, path in sub.artifacts:
                    if Path(path).suffix:
                        touches[path].append(f"{sub.cls.split()[0]}:{verb}")
    out = []
    for path, seq in sorted(touches.items(), key=lambda kv: -len(kv[1]))[:top]:
        compressed = [
            f"{k} x{n}" if n > 1 else k
            for k, n in ((k, len(list(g))) for k, g in __import__("itertools").groupby(seq))
        ]
        out.append((path, len(seq), " -> ".join(compressed[:12])))
    return out


def templates_by_node(
    sessions: list[tuple[str, list[Step], dict[str, list[Sub]]]],
) -> dict[str, list[tuple[str, int]]]:
    per: dict[str, Counter[str]] = defaultdict(Counter)
    for _, steps, subs_by in sessions:
        for step in steps:
            for sub in subs_by[step.id]:
                per[f"{sub.cls}/{sub.program}"][sub.template] += 1
    return {
        node: c.most_common(3)
        for node, c in sorted(per.items(), key=lambda kv: -sum(kv[1].values()))[:16]
    }


# ------------------------------------------------------------------- reporting


def _rows(rows: list[tuple[Any, ...]]) -> str:
    return "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows
    )


def _audit_row(sid: str, s: Step, subs: list[Sub], process: str) -> str:
    esc = html.escape
    parts = []
    for x in subs:
        arts = ", ".join(f"{v} {p}" for v, p in x.artifacts) or "-"
        parts.append(f"<div>{esc(x.cls)}/{esc(x.program)}: {esc(arts)}</div>")
    call = str(s.payload.get("command", s.call_text))[:200]
    return (
        f"<tr><td>{esc(sid[:8])}<br><code>{esc(s.id)}</code></td><td><code>{esc(call)}</code></td>"
        f"<td>{esc(node_at(subs, 2))}<br>outcome: {esc(s.outcome)}<br>process: {esc(process)}</td>"
        f"<td>{''.join(parts)}</td></tr>"
    )


def report_html(meta: dict[str, Any], evalrows: list[dict[str, Any]], ev: dict[str, Any], chains: list[tuple[str, int, str]],
                templates: dict[str, list[tuple[str, int]]], processes: dict[str, tuple[str, float]], trans: list[Transition],
                sessions: list[tuple[str, list[Step], dict[str, list[Sub]]]], violations: list[dict[str, Any]]) -> str:  # fmt: skip
    esc = html.escape
    stats = "".join(
        f"<li><b>{esc(k)}</b>: {esc(json.dumps(v) if isinstance(v, dict) else str(v))}</li>"
        for k, v in meta.items()
    )
    eval_rows = "".join(
        f"<tr><td>{esc(r['granularity'])}</td><td>{esc(r['conditioning'])}</td><td>{r['nodes']}</td><td>{r['edges']}</td><td>{r['contexts']}</td>"
        f"<td><b>{r['top1']:.0%}</b> vs {r['baseline top1']:.0%}</td><td><b>{r['top3']:.0%}</b> vs {r['baseline top3']:.0%}</td></tr>"
        for r in evalrows
    )
    proc_counts = Counter(p for p, _ in processes.values())
    proc_rows = _rows(proc_counts.most_common())
    # top next-step per process type at class/program level
    by_proc: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for t in trans:
        by_proc[t.process][(t.nodes[1], t.nexts[1])] += 1
    proc_edges = "".join(
        f"<h3>{esc(p)}</h3><ol>"
        + "".join(
            f"<li><code>{esc(a)}</code> &rarr; <code>{esc(b)}</code> x{n}</li>"
            for (a, b), n in c.most_common(6)
        )
        + "</ol>"
        for p, c in sorted(by_proc.items(), key=lambda kv: -sum(kv[1].values()))[:5]
    )
    chain_rows = _rows([(p, n, s) for p, n, s in chains])
    tmpl_rows = "".join(
        f"<tr><td><code>{esc(node)}</code></td><td>{''.join(f'<div><code>{esc(t)}</code> x{n}</div>' for t, n in items)}</td></tr>"
        for node, items in templates.items()
    )
    # a composite step example
    example = ""
    for _, steps, subs_by in sessions:
        for step in steps:
            if len(subs_by[step.id]) >= 4 and any(s.artifacts for s in subs_by[step.id]):
                example = (
                    f"<p><code>{esc(str(step.payload.get('command', ''))[:300])}</code></p><ol>"
                    + "".join(
                        f"<li><b>{esc(s.cls)}</b> / {esc(s.program)} -- <code>{esc(s.template)}</code>"
                        + (
                            f" -- {esc(', '.join(f'{v} {p}' for v, p in s.artifacts))}"
                            if s.artifacts
                            else ""
                        )
                        + "</li>"
                        for s in subs_by[step.id]
                    )
                    + "</ol>"
                )
                break
        if example:
            break
    all_steps = [(sid, s, subs_by[s.id]) for sid, steps, subs_by in sessions for s in steps]
    picked = random.Random(11).sample(all_steps, min(20, len(all_steps)))  # nosec B311 - audit sample
    audit = "".join(
        _audit_row(sid, s, subs, processes.get(s.prompt_id, ("-", 0))[0]) for sid, s, subs in picked
    )
    question = (__doc__ or "").split("\n\n")[1]
    ok = "ok" if not violations else "bad"
    return f"""<title>Procedural graph v4: granularity and recall</title>
<style>body{{font:14px system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#222}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}td,th{{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top;font-size:12px}}
th{{background:#f3f3f3}}code{{background:#f4f0ff;padding:0 3px}}h2{{border-bottom:2px solid #6b4fd8;padding-bottom:4px;margin-top:2rem}}
.q{{background:#f4f0ff;padding:1rem;border-left:4px solid #6b4fd8}}.ok{{color:#1a7f37}}.bad{{color:#b42318}}
.score{{display:flex;gap:1.5rem;margin:1rem 0;flex-wrap:wrap}}.score div{{background:#fafafa;border:1px solid #ddd;padding:.6rem 1rem}}.score b{{font-size:1.4rem;display:block}}</style>
<h1>Which procedural graph is worth storing? (v4)</h1>
<p class=q>{esc(question)}</p>
<h2>Corpus and gates</h2><ul>{stats}</ul>
<div class=score>
<div><b>{meta.get("triplets", "")}</b>triplets</div>
<div class={ok}><b>{len(violations)}</b>domain/range violations</div>
<div><b>{meta.get("transitions", "")}</b>within-prompt transitions</div>
</div>
<h2>Held-out next-step recall: granularity x conditioning</h2>
<p>Train on ~75% of prompts, test on the rest. Accuracy of the top-1 / top-3 predicted next node vs the majority-class baseline at the same granularity.</p>
<table><tr><th>granularity</th><th>conditioning</th><th>nodes</th><th>edges</th><th>contexts</th><th>top-1 vs baseline</th><th>top-3 vs baseline</th></tr>{eval_rows}</table>
<h2>Edit &rarr; verify rate</h2><ul>{"".join(f"<li><b>{esc(k)}</b>: {esc(json.dumps(v) if isinstance(v, dict) else str(v))}</li>" for k, v in ev.items())}</ul>
<h2>Process type of prompts (GLiNER2 over the user's text)</h2>
<table><tr><th>process</th><th>prompts</th></tr>{proc_rows}</table>
<h3>Most common transitions per process type (class/program)</h3>{proc_edges}
<h2>Artifact chains: what happens to the most-touched files</h2>
<table><tr><th>file</th><th>touches</th><th>sequence (class:verb)</th></tr>{chain_rows}</table>
<h2>Action templates per node (structured guidance)</h2>
<table><tr><th>node</th><th>top templates</th></tr>{tmpl_rows}</table>
<h2>A composite step, decomposed</h2>{example}
<h2>Random audit sample: 20 steps across sessions</h2>
<table><tr><th>session / step</th><th>call</th><th>node (class/program/ext)</th><th>sub-activities and artifacts</th></tr>{audit}</table>
"""


# ----------------------------------------------------------------------- main


def project_sessions(n: int) -> list[Path]:
    key = str(Path.cwd()).replace(":", "-").replace("\\", "-").replace("/", "-")
    files = sorted(
        (Path.home() / ".claude" / "projects" / key).glob("*.jsonl"),
        key=lambda p: -p.stat().st_size,
    )
    return files[:n]


def main(argv: list[str]) -> None:
    n_sessions = int(argv[argv.index("--sessions") + 1]) if "--sessions" in argv else 12
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    t0 = time.perf_counter()
    onto, meta = load_ontologies()
    onto.add_relation(
        "composed of", "Performed Activity", "Performed Activity", "ACE"
    )  # step -> sub-activity
    onto.add_class("Intended Process", "SPO")

    sessions: list[tuple[str, list[Step], dict[str, list[Sub]]]] = []
    texts: dict[str, str] = {}
    offset = 0
    gl_meta: Counter[str] = Counter()
    for path in project_sessions(n_sessions):
        steps = steps_from(path, limit)
        if not steps:
            continue
        for s in steps:
            s.index += offset
        offset += len(steps)
        subs_by = {s.id: subs_of(s) for s in steps}
        for s in steps:
            s.activities = [(x.cls, x.program) for x in subs_by[s.id]]
        gl = run_gliner(steps)  # intents + GLiNER2 fallback for unknown commands
        gl_meta.update(gl)
        for s in steps:  # fold a GLiNER2-resolved class into the Unknown sub-activities
            resolved = next((c for c, src in s.activities if src == "gliner2"), "")
            for x in subs_by[s.id]:
                if resolved and x.cls == "Unknown Activity":
                    x.cls = resolved
        texts.update(prompt_texts(path))
        sessions.append((path.stem[:8], steps, subs_by))
    processes = type_prompts(texts) if texts else {}
    t1 = time.perf_counter()

    triplets = map_corpus(sessions, processes)
    violations = check_consistency(triplets, onto)
    trans = transitions(sessions, processes)
    evalrows = evaluate(trans)
    ev = edit_verify_rate(sessions)
    chains = artifact_chains(sessions)
    templates = templates_by_node(sessions)
    total_steps = sum(len(s) for _, s, _ in sessions)
    meta = {
        "sessions": len(sessions), "tool calls (steps)": total_steps, "prompts typed": len(processes), **meta, **dict(gl_meta),
        "triplets": len(triplets), "domain/range violations": len(violations), "transitions": len(trans),
        "seconds total / of which GLiNER2+parse": f"{time.perf_counter() - t0:.0f} / {t1 - t0:.0f}", "LLM calls": 0,
    }  # fmt: skip
    for k, v in meta.items():
        print(f"{k:38s} {v}")
    print("\nheld-out next-step recall:")
    for r in evalrows:
        print(
            f"  {r['granularity']:20s} {r['conditioning']:14s} nodes={r['nodes']:4d} edges={r['edges']:5d} top1={r['top1']:.0%} (base {r['baseline top1']:.0%}) top3={r['top3']:.0%} (base {r['baseline top3']:.0%})"
        )
    print("edit->verify:", ev)
    print("process types:", Counter(p for p, _ in processes.values()).most_common())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "procedural_v4.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "evaluation": evalrows,
                "edit_verify": ev,
                "chains": chains,
                "templates": templates,
                "triplets": triplets[:5000],
            },
            indent=1,
            default=str,
        ),
        encoding="utf-8",
    )
    (OUT_DIR / "procedural_v4.html").write_text(
        report_html(meta, evalrows, ev, chains, templates, processes, trans, sessions, violations),
        encoding="utf-8",
    )
    print(f"\nwrote {OUT_DIR / 'procedural_v4'}.{{json,html}}")


if __name__ == "__main__":
    main(sys.argv[1:])
