"""Compare what the assistant applied with what the document became (SP4).

Pure functions — no database, no clock. feedback_report --changes fetches the
rows (memory/document_store.py) and prints what build_document_changes returns.
Spec: docs/superpowers/specs/2026-10-15-document-changes-design.md, "Reading it".

Verdicts are exact after normalisation (spec D8): no similarity score decides
anything. Shared words only pick the paragraph printed beside an `altered`
edit, for a person to judge.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from typing import Callable

# The pane's normalize.ts rules — curly quotes straightened, non-breaking spaces
# made plain — plus every whitespace run collapsed, so Word curling a quote or
# re-wrapping a line is not an alteration.
_STRAIGHTEN = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " ", "\u202f": " ",
})
_WHITESPACE = re.compile(r"\s+")

KEPT, ALTERED, REVERTED, NO_LATER = "kept", "altered", "reverted", "no later capture"
VERDICTS = (KEPT, ALTERED, REVERTED, NO_LATER)          # the order they are counted in
MOMENTS = ("pane_open", "idle", "finalize")
PARAGRAPHS_SHOWN = 20
_TRACKED_KIND = {"Added": "ins", "Deleted": "del", "Formatted": "fmt"}
_KIND_ORDER = ("ins", "del", "fmt", "other")


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text or "").translate(_STRAIGHTEN)).strip()


def paragraphs(text: str) -> list[str]:
    """The non-empty paragraphs, normalised. splitlines() splits on Word's \\r
    as well as on \\n and \\r\\n."""
    return [p for p in (normalize(line) for line in (text or "").splitlines()) if p]


def _verdict(applied: dict, final: str) -> str:
    """edit_verdict against an already-normalised text."""
    action = applied.get("action", "")
    new = normalize(applied.get("new_text", ""))
    target = normalize(applied.get("target_text", ""))
    if action == "delete":
        if not target:
            return ALTERED
        return REVERTED if target in final else KEPT
    if action in ("replace", "replace_all") and not new:
        # A replacement with nothing: kept while the old wording stays gone.
        return REVERTED if target and target in final else KEPT
    if new and new in final:
        return KEPT
    if action in ("replace", "replace_all") and target and target in final:
        return REVERTED
    if action == "redline":
        anchors = [normalize(a) for a in applied.get("target_text", "").split("\n") if a.strip()]
        if anchors and all(a in final for a in anchors):
            return REVERTED
    return ALTERED


def edit_verdict(applied: dict, final_text: str) -> str:
    """kept / reverted / altered for one applied edit against a later text (the
    spec's verdict table). Whether there IS a later text is the caller's call."""
    return _verdict(applied, normalize(final_text))


def closest_paragraph_finder(text: str) -> Callable[[str], str]:
    """A function giving the paragraph of `text` sharing the most words with a
    needle (Jaccard over word sets) — printed beside an `altered` edit; it
    decides nothing. The word sets are built once per text, so a 2,000-paragraph
    contract with a hundred altered edits stays fast."""
    paras = paragraphs(text)
    words = [set(p.lower().split()) for p in paras]

    def closest(needle: str) -> str:
        mine = set(normalize(needle).lower().split())
        if not paras or not mine:
            return ""

        def overlap(i: int) -> float:
            union = len(mine | words[i])
            return len(mine & words[i]) / union if union else 0.0

        return paras[max(range(len(paras)), key=overlap)]

    return closest


def changed_paragraphs(first: str, last: str, applied: list[dict]) -> list[dict]:
    """Paragraphs only in the first text (-) or only in the last (+). A `+` is the
    assistant's when it holds an applied text; a `-` when it holds what an
    applied replacement, deletion or redline took out (an insert's target is
    only its anchor). Everything else is `not suggested`. Old and new are never
    paired into one line: a clause typed where an applied deletion was would
    inherit the deletion's label."""
    old, new = paragraphs(first), paragraphs(last)
    put_in = [t for t in (normalize(a.get("new_text", "")) for a in applied) if t]
    taken_out: list[str] = []
    for a in applied:
        if a.get("action") == "insert":
            continue
        target = a.get("target_text", "")
        lines = target.split("\n") if a.get("action") == "redline" else [target]
        taken_out += [t for t in (normalize(line) for line in lines) if t]

    out: list[dict] = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for text in old[i1:i2]:
            out.append({"mark": "-", "text": text, "label": "assistant"
                        if any(t in text for t in taken_out) else "not suggested"})
        for text in new[j1:j2]:
            out.append({"mark": "+", "text": text, "label": "assistant"
                        if any(t in text for t in put_in) else "not suggested"})
    return out


def _by_author(changes: list[dict]) -> dict[str, Counter]:
    authors: dict[str, Counter] = {}
    for change in changes:
        kind = _TRACKED_KIND.get(change.get("type", ""), "other")
        authors.setdefault(change.get("author") or "(no author)", Counter())[kind] += 1
    return authors


def build_document_changes(document_id: str, observations: list[dict],
                           versions: dict[str, dict], applied: list[dict]) -> dict:
    """Everything --changes prints for one document.

    observations — every sighting of the text, oldest first: timestamp, kind
      ('turn' or a capture moment), sha256, attorney_id, user_name,
      tracked_changes, tracked_total (memory/document_store.observations).
    versions — sha256 → {text, truncated} (memory/document_store.versions).
    applied — Apply events with their text, oldest first: timestamp, turn_id,
      applied (memory/document_store.applied_events).
    """
    stored = [o for o in observations if o["sha256"] in versions]
    kinds = Counter(o["kind"] for o in observations)
    names = [o["user_name"] for o in observations if o.get("user_name")]
    result: dict = {
        "document_id": document_id,
        "attorneys": sorted({o["attorney_id"] for o in observations if o["attorney_id"]}),
        "name": names[-1] if names else "",
        "first": observations[0]["timestamp"] if observations else "",
        "last": observations[-1]["timestamp"] if observations else "",
        "counts": {kind: kinds.get(kind, 0) for kind in ("turn", *MOMENTS)},
        "distinct": len({o["sha256"] for o in observations if o["sha256"]}),
        "first_stored": None, "skipped": 0, "truncated": False,
        "edits": [], "tracked": None, "paragraphs": [],
    }
    final = ""
    closest: Callable[[str], str] = lambda _needle: ""  # noqa: E731
    if stored:
        first, last = stored[0], stored[-1]
        result["first_stored"] = {"timestamp": first["timestamp"], "kind": first["kind"]}
        result["skipped"] = next(i for i, o in enumerate(observations) if o is first)
        result["truncated"] = any(versions[o["sha256"]].get("truncated") for o in stored)
        final_text = versions[last["sha256"]]["text"]
        final = normalize(final_text)
        closest = closest_paragraph_finder(final_text)
        result["paragraphs"] = changed_paragraphs(
            versions[first["sha256"]]["text"], final_text, [e["applied"] for e in applied])
    for event in applied:
        a = event["applied"]
        if stored and stored[-1]["timestamp"] > event["timestamp"]:
            verdict = _verdict(a, final)
            near = closest(a.get("new_text", "")) if verdict == ALTERED else ""
        else:
            verdict, near = NO_LATER, ""
        result["edits"].append({
            "verdict": verdict, "action": a.get("action", ""),
            "target_text": a.get("target_text", ""), "new_text": a.get("new_text", ""),
            "edited": bool(a.get("proposed_text")), "turn_id": event.get("turn_id", ""),
            "closest": near,
        })
    captures = [o for o in observations if o["kind"] != "turn"]
    if captures:
        latest = captures[-1]
        changes = latest["tracked_changes"] or []
        result["tracked"] = {"total": latest["tracked_total"], "listed": len(changes),
                             "by_author": _by_author(changes)}
    return result


def _head(text: str, width: int, full: bool) -> str:
    return text if full or len(text) <= width else text[: width - 1] + "…"


def _tail(text: str, width: int, full: bool) -> str:
    return text if full or len(text) <= width else "…" + text[-(width - 1):]


def _minute(timestamp: str) -> str:
    return timestamp[:16].replace("T", " ")


def format_document_changes(d: dict, *, full: bool = False) -> list[str]:
    """One document's block, as feedback_report --changes prints it."""
    who = ", ".join(d["attorneys"]) or "—"
    if d["name"]:
        who = f"{d['name']} ({who})"
    c = d["counts"]
    lines = [
        f"document {d['document_id']}  {who} · {_minute(d['first'])} → {_minute(d['last'])}",
        f"  observations {sum(c.values())} ({c['turn']} turns · {c['pane_open']} pane_open · "
        f"{c['idle']} idle · {c['finalize']} finalize) · {d['distinct']} distinct texts",
    ]
    if d["first_stored"] is None:
        lines.append("  no stored text yet — every observation has only a fingerprint")
        return lines
    first, skipped = d["first_stored"], d["skipped"]
    note = (f" — {skipped} earlier observation{' has' if skipped == 1 else 's have'} "
            "only a fingerprint" if skipped else "")
    lines.append(f"  first stored text {_minute(first['timestamp'])} ({first['kind']}){note}")
    if d["truncated"]:
        lines.append("  ⚠ a stored text was cut at 1,000,000 characters — "
                     "edits past the cut read as altered")

    tally = Counter(e["verdict"] for e in d["edits"])
    lines.append(f"  applied edits {len(d['edits'])}: "
                 + " · ".join(f"{tally.get(v, 0)} {v}" for v in VERDICTS))
    for e in d["edits"]:
        what = f"\"{_head(e['target_text'], 40, full)}\""
        if e["action"] != "delete":
            what += f" → \"{_tail(e['new_text'], 40, full)}\""
        edited = "   (edited before applying)" if e["edited"] else ""
        turn = f"   turn {e['turn_id'][:8]}" if e["turn_id"] else ""
        lines.append(f"    {e['verdict']:<17}{e['action']:<12}{what}{edited}{turn}")
        if e["closest"]:
            lines.append(f"{'':<21}closest now: \"{_head(e['closest'], 70, full)}\"")

    t = d["tracked"]
    if t is None:
        lines.append("  tracked changes: no pane capture yet")
    elif t["total"] < 0:
        lines.append("  tracked changes at the last capture: unreadable in that Word")
    else:
        authors = sorted(t["by_author"].items(), key=lambda item: (-sum(item[1].values()), item[0]))
        parts = [f"{author} {sum(kinds.values())} ("
                 + ", ".join(f"{kinds[k]} {k}" for k in _KIND_ORDER if kinds.get(k)) + ")"
                 for author, kinds in authors]
        listed = f" (first {t['listed']} listed)" if t["listed"] < t["total"] else ""
        lines.append(f"  tracked changes at the last capture: {t['total']}{listed}"
                     + (" — " + " · ".join(parts) if parts else ""))

    p = d["paragraphs"]
    mine = sum(1 for x in p if x["label"] == "assistant")
    lines.append(f"  paragraphs changed since the first text: {len(p)} — "
                 f"{mine} explained by an applied edit · {len(p) - mine} not suggested")
    shown = p if full else p[:PARAGRAPHS_SHOWN]
    for x in shown:
        lines.append(f"    {x['mark']} \"{_head(x['text'], 70, full)}\"   {x['label']}")
    if len(shown) < len(p):
        lines.append(f"    … {len(p) - len(shown)} more — --full lists them all")
    return lines
