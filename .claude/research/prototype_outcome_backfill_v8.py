"""PROTOTYPE -- throwaway. What the procedural graph looks like WITH an outcome.

The live store has 7015 steps and not one is anything but ``neutral``:
``outcome`` is neutral for every row, ``symbol_ref`` is 0% populated,
``result_snippet`` is 2 rows of fixture text, every sequence is
``process_type=Unknown`` and ``derived_outcome=neutral``. So the procedural graph
is a Markov chain over tool names with no notion of better or worse -- which is
exactly what ``recall`` returns: N rows of "usually goes to", ranked by support.

But the outcome exists. It is in the session transcripts, which record
``is_error`` on every tool_result, including the permission denials and user
rejections that the epic calls PitfallKind.REJECTED.

Question: if the outcome were ingested, would the graph say anything the
support-ranked graph cannot?

Reads, never writes: the Claude Code transcripts and the episodes store (read-only).
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

TRANSCRIPTS = pathlib.Path.home() / ".claude" / "projects" / "C--Users-User-Documents-dev-processrecall"
STORE = pathlib.Path.home() / ".processrecall" / "episodes.db"

#: How the store buckets a tool into activity_class, near enough for this test.
_CLASS = {
    "Read": "Inspection", "Glob": "Search", "Grep": "Search", "Edit": "ChangeImplementation",
    "Write": "ChangeImplementation", "NotebookEdit": "ChangeImplementation",
    "TodoWrite": "Planning", "Task": "Delegation", "WebFetch": "Research",
    "WebSearch": "Research", "ToolSearch": "Search",
}
_BASH_CLASS = {
    "pytest": "ArtifactEvaluation", "ruff": "ArtifactEvaluation", "mypy": "ArtifactEvaluation",
    "python": "ScriptExecution", "git": "Inspection", "grep": "Search", "rg": "Search",
    "ls": "Inspection", "cat": "Inspection", "head": "Inspection", "tail": "Inspection",
    "sed": "Inspection", "rm": "EnvironmentConfiguration", "mkdir": "EnvironmentConfiguration",
    "docker": "EnvironmentConfiguration", "npm": "EnvironmentConfiguration",
}


@dataclass(slots=True)
class Move:
    node: str
    tool: str
    signature: str
    outcome: str          # ok | failure | rejected
    detail: str


def _node(tool: str, inp: dict[str, Any]) -> tuple[str, str]:
    """(node_key, signature) the way the store's class/program level would name it."""
    if tool == "Bash":
        cmd = str(inp.get("command", "")).strip()
        head = cmd.split()[0] if cmd.split() else "?"
        head = head.split("/")[-1]
        if head in {"cd", "sudo", "env"} and len(cmd.split()) > 1:
            head = cmd.split()[1].split("/")[-1]
        return f"{_BASH_CLASS.get(head, 'Unknown')}/{head}", cmd[:90]
    cls = _CLASS.get(tool, "Unknown")
    sig = inp.get("file_path") or inp.get("pattern") or inp.get("query") or ""
    return f"{cls}/{tool}", str(sig)[:90]


def read_moves() -> list[list[Move]]:
    """Every session as an ordered list of moves, with the outcome attached."""
    sessions: list[list[Move]] = []
    for f in sorted(TRANSCRIPTS.glob("*.jsonl")):
        uses: dict[str, tuple[str, str, str]] = {}
        order: list[str] = []
        results: dict[str, tuple[str, str]] = {}
        for line in f.open(encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = d.get("message") or {}
            content = msg.get("content") if isinstance(msg.get("content"), list) else []
            for c in content:
                if c.get("type") == "tool_use":
                    node, sig = _node(c.get("name", "?"), c.get("input") or {})
                    uses[c.get("id")] = (node, c.get("name", "?"), sig)
                    order.append(c.get("id"))
                elif c.get("type") == "tool_result":
                    txt = str(c.get("content"))
                    if not c.get("is_error"):
                        results[c.get("tool_use_id")] = ("ok", "")
                    elif "Permission" in txt or "doesn't want to proceed" in txt:
                        results[c.get("tool_use_id")] = ("rejected", txt.split("\n")[0][:110])
                    else:
                        results[c.get("tool_use_id")] = ("failure", txt.split("\n")[0][:110])
        moves = []
        for tid in order:
            if tid not in uses:
                continue
            node, tool, sig = uses[tid]
            outcome, detail = results.get(tid, ("ok", ""))
            moves.append(Move(node, tool, sig, outcome, detail))
        if moves:
            sessions.append(moves)
    return sessions


def build(sessions: list[list[Move]]) -> dict[str, Any]:
    """The transition graph, carrying outcome counts on the TARGET of each move."""
    support: Counter = Counter()
    target_outcome: dict[str, Counter] = defaultdict(Counter)
    rejected_sig: dict[str, Counter] = defaultdict(Counter)
    failed_sig: dict[str, Counter] = defaultdict(Counter)
    for moves in sessions:
        for a, b in zip(moves, moves[1:]):
            support[(a.node, b.node)] += 1
            target_outcome[b.node][b.outcome] += 1
        for m in moves:
            if m.outcome == "rejected":
                rejected_sig[m.node][m.signature] += 1
            elif m.outcome == "failure":
                failed_sig[m.node][m.signature] += 1
    return {
        "support": support, "target_outcome": target_outcome,
        "rejected_sig": rejected_sig, "failed_sig": failed_sig,
    }


def pitfalls(g: dict[str, Any], floor: int = 2) -> list[dict[str, Any]]:
    """The moves a run should be warned about -- the epic's PitfallKind.REJECTED."""
    out = []
    for node, sigs in g["rejected_sig"].items():
        total = sum(sigs.values())
        if total < floor:
            continue
        top, n = sigs.most_common(1)[0]
        out.append({"node": node, "kind": "REJECTED", "support": total,
                    "worst": top, "worst_n": n})
    for node, sigs in g["failed_sig"].items():
        repeats = {s: n for s, n in sigs.items() if n >= floor}
        if not repeats:
            continue
        top, n = max(repeats.items(), key=lambda kv: kv[1])
        out.append({"node": node, "kind": "REPEATED_FAILURE", "support": sum(repeats.values()),
                    "worst": top, "worst_n": n})
    return sorted(out, key=lambda r: -r["support"])


def recall_with_outcome(g: dict[str, Any], node: str, limit: int = 6) -> list[str]:
    """What recall could say for *node* if the outcome were ingested."""
    nxt = [(b, n) for (a, b), n in g["support"].items() if a == node]
    nxt.sort(key=lambda kv: -kv[1])
    lines = []
    for target, n in nxt[:limit]:
        oc = g["target_outcome"].get(target, Counter())
        tot = sum(oc.values()) or 1
        bad = oc["failure"] + oc["rejected"]
        risk = f", {100 * bad / tot:.0f}% of them failed or were refused" if bad else ""
        lines.append(f"after {node} the work usually goes to {target} ({n} seen{risk})")
    for p in pitfalls(g):
        if p["node"] == node:
            lines.append(
                f"PITFALL {p['kind']}: {p['support']} times here; worst repeat x{p['worst_n']}: {p['worst'][:70]}")
    return lines


def store_reality() -> dict[str, Any]:
    con = sqlite3.connect(f"file:{STORE.as_uri()[8:]}?mode=ro", uri=True)
    n = con.execute("SELECT count(*) FROM steps").fetchone()[0]
    r = {
        "steps": n,
        "outcome distribution": dict(con.execute("SELECT outcome,count(*) FROM steps GROUP BY outcome")),
        "symbol_ref populated": con.execute(
            "SELECT count(*) FROM steps WHERE symbol_ref IS NOT NULL AND symbol_ref!=''").fetchone()[0],
        "result_snippet populated": con.execute(
            "SELECT count(*) FROM steps WHERE result_snippet IS NOT NULL AND result_snippet!=''").fetchone()[0],
        "sequence process_type": dict(con.execute("SELECT process_type,count(*) FROM sequences GROUP BY process_type")),
    }
    con.close()
    return r


def main() -> None:
    sessions = read_moves()
    g = build(sessions)
    oc = Counter()
    for moves in sessions:
        for m in moves:
            oc[m.outcome] += 1

    print("== what the live store believes ==")
    for k, v in store_reality().items():
        print(f"  {k}: {v}")

    print("\n== what the transcripts actually record ==")
    print(f"  sessions: {len(sessions)}   moves: {sum(oc.values())}")
    print(f"  outcome distribution: {dict(oc)}")
    print(f"  failure+rejection rate: {100 * (oc['failure'] + oc['rejected']) / max(1, sum(oc.values())):.1f}%")

    print("\n== pitfalls that would derive, that today cannot ==")
    for p in pitfalls(g)[:10]:
        print(f"  [{p['kind']:17}] {p['node']:32} x{p['support']:<3} worst x{p['worst_n']}: {p['worst'][:60]}")

    print("\n== recall('ScriptExecution/python') -- with the outcome ingested ==")
    for line in recall_with_outcome(g, "ScriptExecution/python"):
        print(f"  {line}")

    print("\n== recall('ChangeImplementation/Edit') -- with the outcome ingested ==")
    for line in recall_with_outcome(g, "ChangeImplementation/Edit"):
        print(f"  {line}")

    out = pathlib.Path(__file__).parent / "out" / "outcome_backfill_v8.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "store": store_reality(),
        "transcript outcomes": dict(oc),
        "pitfalls": pitfalls(g),
    }, indent=1), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
