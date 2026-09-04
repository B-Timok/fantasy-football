"""Optional: pull picks from a live Sleeper draft instead of typing them.

Sleeper's draft API is public and needs no login:
  GET https://api.sleeper.app/v1/draft/<draft_id>            -> draft settings
  GET https://api.sleeper.app/v1/draft/<draft_id>/picks      -> picks so far
  GET https://api.sleeper.app/v1/user/<username>             -> user id (to find your slot)
The draft_id is the number in the draft URL: sleeper.com/draft/nfl/<draft_id>.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

from .models import normalize_name, normalize_pos

BASE = "https://api.sleeper.app/v1"


def _get(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "draftkit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_draft(draft_id: str) -> dict:
    return _get(f"{BASE}/draft/{draft_id}")


def fetch_picks(draft_id: str) -> list[dict]:
    return _get(f"{BASE}/draft/{draft_id}/picks")


def fetch_user_id(username: str) -> Optional[str]:
    d = _get(f"{BASE}/user/{username}")
    return d.get("user_id") if d else None


def slot_for_user(draft: dict, user_id: str) -> Optional[int]:
    order = draft.get("draft_order") or {}
    slot = order.get(user_id)
    return int(slot) if slot is not None else None


def parse_picks(raw: list[dict]) -> list[dict]:
    """Turn Sleeper's pick objects into {pick_no, key, name, pos, slot}."""
    out = []
    for p in sorted(raw, key=lambda p: p.get("pick_no", 0)):
        md = p.get("metadata") or {}
        first, last = md.get("first_name", ""), md.get("last_name", "")
        pos = normalize_pos(md.get("position", "") or "")
        if pos == "DEF" and not first:
            # Sleeper defenses come through as e.g. first_name="Dallas", last_name="Cowboys"
            name = last
        else:
            name = f"{first} {last}".strip()
        out.append({
            "pick_no": int(p.get("pick_no", len(out) + 1)),
            "key": normalize_name(name),
            "name": name,
            "pos": pos or "",
            "slot": int(p.get("draft_slot", 0) or 0),
        })
    return out
