"""Well range parsing and validation for liquid handler deck positions."""

from __future__ import annotations

import re


def parse_well_range(spec: str) -> list[str]:
    """Parse a well range string into individual well positions.

    Supports:
      "A1"       -> ["A1"]
      "A1-A6"    -> ["A1", "A2", "A3", "A4", "A5", "A6"]  (same row)
      "A1-H1"    -> ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]  (same col)
    """
    spec = spec.strip().upper()
    m = re.match(r"^([A-H])(\d{1,2})-([A-H])(\d{1,2})$", spec)
    if not m:
        if re.match(r"^[A-H]\d{1,2}$", spec):
            return [spec]
        raise ValueError(f"Invalid well specification: {spec!r}")

    r1, c1, r2, c2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))

    if r1 == r2:
        lo, hi = sorted([c1, c2])
        return [f"{r1}{c}" for c in range(lo, hi + 1)]
    elif c1 == c2:
        lo, hi = sorted([ord(r1), ord(r2)])
        return [f"{chr(r)}{c1}" for r in range(lo, hi + 1)]
    else:
        raise ValueError(
            f"Invalid well range {spec!r}: both row and column change. "
            "Ranges must vary along one axis only (e.g. A1-A6 or A1-H1)."
        )


def validate_wells(
    wells: list[str], max_row: str = "H", max_col: int = 12
) -> list[str]:
    """Return list of invalid well positions."""
    invalid = []
    for w in wells:
        w = w.upper()
        m = re.match(r"^([A-Z])(\d{1,2})$", w)
        if not m:
            invalid.append(w)
            continue
        row, col = m.group(1), int(m.group(2))
        if row > max_row or col < 1 or col > max_col:
            invalid.append(w)
    return invalid


def expand_well_list(raw: list[str]) -> list[str]:
    """Expand a list that may contain range strings into individual wells."""
    result = []
    for item in raw:
        result.extend(parse_well_range(item))
    return result
