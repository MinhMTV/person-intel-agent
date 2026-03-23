"""Dossier diff module — compare two scan results of the same person."""

from __future__ import annotations

from typing import Any


def diff_dossiers(old: dict, new: dict) -> dict:
    """Compare two dossier snapshots and return differences.

    Args:
        old: Previous dossier data (from model_dump or API)
        new: Current dossier data

    Returns:
        Dict with added, removed, and changed items per category
    """
    result = {
        "has_changes": False,
        "social_profiles": {"added": [], "removed": [], "count_change": 0},
        "web_results": {"added": [], "removed": [], "count_change": 0},
        "image_matches": {"added": [], "removed": [], "count_change": 0},
        "email_addresses": {"added": [], "removed": [], "count_change": 0},
        "professional": {"added": [], "removed": [], "count_change": 0},
        "confidence_change": 0.0,
    }

    old_conf = old.get("confidence_score", 0)
    new_conf = new.get("confidence_score", 0)
    result["confidence_change"] = round(new_conf - old_conf, 4)

    def _url_key(items: list, key: str = "url") -> dict[str, Any]:
        """Index items by URL."""
        return {item.get(key, ""): item for item in items if item.get(key)}

    def _email_key(items: list) -> set[str]:
        return {e if isinstance(e, str) else e.get("email", "") for e in items}

    for category in ["social_profiles", "web_results", "image_matches", "professional"]:
        old_items = old.get(category, [])
        new_items = new.get(category, [])
        old_urls = _url_key(old_items)
        new_urls = _url_key(new_items)

        added = [v for k, v in new_urls.items() if k not in old_urls]
        removed = [v for k, v in old_urls.items() if k not in new_urls]

        result[category]["added"] = added
        result[category]["removed"] = removed
        result[category]["count_change"] = len(new_items) - len(old_items)

        if added or removed:
            result["has_changes"] = True

    # Email addresses (simple lists)
    old_emails = _email_key(old.get("email_addresses", []))
    new_emails = _email_key(new.get("email_addresses", []))
    result["email_addresses"]["added"] = sorted(new_emails - old_emails)
    result["email_addresses"]["removed"] = sorted(old_emails - new_emails)
    result["email_addresses"]["count_change"] = len(new_emails) - len(old_emails)
    if result["email_addresses"]["added"] or result["email_addresses"]["removed"]:
        result["has_changes"] = True

    if result["confidence_change"] != 0:
        result["has_changes"] = True

    return result


def diff_summary(diff: dict) -> str:
    """Generate a human-readable diff summary."""
    lines = []
    if not diff.get("has_changes"):
        return "No changes detected."

    if diff["confidence_change"] != 0:
        direction = "↑" if diff["confidence_change"] > 0 else "↓"
        lines.append(f"Confidence: {direction} {abs(diff['confidence_change']):.1%}")

    for cat in ["social_profiles", "web_results", "image_matches", "email_addresses", "professional"]:
        d = diff.get(cat, {})
        added = len(d.get("added", []))
        removed = len(d.get("removed", []))
        if added or removed:
            name = cat.replace("_", " ").title()
            parts = []
            if added:
                parts.append(f"+{added} added")
            if removed:
                parts.append(f"-{removed} removed")
            lines.append(f"{name}: {', '.join(parts)}")

    return "\n".join(lines) if lines else "No changes detected."
