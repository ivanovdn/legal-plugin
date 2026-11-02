# ingest/books/overrides.py
"""Hand fixes to a book's parser, one file per book and edition.

Every fix carries its reason, so a reader of the file can see why the parser was
overruled. `match` and `value` mean, by kind:

  label_level   match: a line prefix                 value: the level
  hyphen        match: "<word->|<next word>"         value: "join" or "keep"
  xref_target   match: the raw reference text        value: the target path
  text_owner    match: a line prefix                 value: the path of the unit the line's text is
                                                            (flush text set at another unit's indent)
  note_home     match: a note's path ("ch1-n15")     value: the path of the node the note is about
                page: the page the note starts on           (its marker closes a definition's last item)

A fix that nothing consumed is reported by the ledger, so a stale one (the parser
learned to do it alone, or the page moved) cannot sit in the file unnoticed.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

OVERRIDE_KINDS = frozenset({"label_level", "hyphen", "xref_target", "text_owner", "note_home"})
HYPHEN_VALUES = frozenset({"join", "keep"})
PREFIX_KINDS = frozenset({"label_level", "text_owner"})  # their match is a line prefix
_PATH_RE = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*")  # a path, as in a ref_id
_NOTE_PATH_RE = re.compile(r"(?:ch\d+|app[A-Z])-n\d+")  # a history note's path: ch1-n15, appB-n2
PATH_VALUE_KINDS = frozenset({"text_owner", "note_home", "xref_target"})  # their value is a path


@dataclass(frozen=True)
class Override:
    id: str
    kind: str
    page: int
    match: str
    value: str
    reason: str


def _parse(entry: object, index: int) -> Override:
    if not isinstance(entry, dict):
        raise ValueError(f"override #{index}: expected an object, got {type(entry).__name__}")
    label = f"override {entry.get('id')!r} (#{index})"
    for key in ("id", "kind", "page", "match", "value", "reason"):
        if key not in entry:
            raise ValueError(f"{label}: missing field {key!r}")
    for key in ("id", "kind", "match", "value", "reason"):
        if not isinstance(entry[key], str):
            raise ValueError(f"{label}: field {key!r} must be a string")
    if isinstance(entry["page"], bool) or not isinstance(entry["page"], int):
        raise ValueError(f"{label}: field 'page' must be an integer")
    if not entry["id"].strip():
        raise ValueError(f"override #{index}: field 'id' is empty")
    if entry["kind"] not in OVERRIDE_KINDS:
        raise ValueError(
            f"{label}: unknown kind {entry['kind']!r}; expected one of {sorted(OVERRIDE_KINDS)}"
        )
    if not entry["reason"].strip():
        raise ValueError(f"{label}: needs a reason")
    if not entry["match"]:
        raise ValueError(f"{label}: field 'match' is empty")
    if entry["kind"] == "hyphen" and entry["value"] not in HYPHEN_VALUES:
        raise ValueError(
            f"{label}: hyphen value must be one of {sorted(HYPHEN_VALUES)}, got {entry['value']!r}"
        )
    if entry["kind"] in PATH_VALUE_KINDS and not _PATH_RE.fullmatch(entry["value"]):
        raise ValueError(f"{label}: {entry['kind']} value must be a path (as in a ref_id), got {entry['value']!r}")
    if entry["kind"] == "note_home" and not _NOTE_PATH_RE.fullmatch(entry["match"]):
        raise ValueError(f"{label}: note_home match must be a note's path (ch1-n15, appB-n2), got {entry['match']!r}")
    return Override(
        id=entry["id"],
        kind=entry["kind"],
        page=entry["page"],
        match=entry["match"],
        value=entry["value"],
        reason=entry["reason"],
    )


def load_overrides(path: Path) -> list[Override]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of overrides, got {type(raw).__name__}")
    overrides = [_parse(entry, i) for i, entry in enumerate(raw)]
    seen: set[str] = set()
    for override in overrides:
        if override.id in seen:
            raise ValueError(f"{path}: duplicate override id {override.id!r}")
        seen.add(override.id)
    return overrides


class OverrideLedger:
    """Looks overrides up while a parse runs, and remembers which ones it handed out."""

    def __init__(self, overrides: list[Override]):
        self._overrides = list(overrides)
        self._used: set[str] = set()

    def find(self, kind: str, page: int, text: str) -> Override | None:
        """The override for this kind, page and text, marked used; None if there is none.

        A `label_level` or `text_owner` match is a line prefix; the other kinds match the whole text.
        """
        for override in self._overrides:
            if override.kind != kind or override.page != page:
                continue
            hit = text.startswith(override.match) if kind in PREFIX_KINDS else text == override.match
            if hit:
                self._used.add(override.id)
                return override
        return None

    def unused(self) -> list[Override]:
        return [o for o in self._overrides if o.id not in self._used]
