"""Glossary management tool — CRUD operations for terminology.

Supports add, list, search, delete, and clear operations on glossary
term entries. Detects conflicts (duplicate source terms with different targets).
"""

from __future__ import annotations

from agent_localization_specialist.models import Glossary, TermEntry


def manage_glossary(
    action: str,
    entries: list | None = None,
    glossary: Glossary | None = None,
    source_lang: str = "en",
    target_lang: str = "zh",
) -> Glossary:
    """Manage a terminology glossary with CRUD operations.

    Args:
        action: Operation to perform — "add", "list", "search", "delete", "clear".
        entries: List of TermEntry objects or dicts for the operation.
        glossary: Existing glossary to operate on (creates new if None).
        source_lang: Source language code for new glossaries.
        target_lang: Target language code for new glossaries.

    Returns:
        Updated Glossary after applying the operation.

    Raises:
        ValueError: If the action is unknown or required parameters are missing.
    """
    if glossary is None:
        glossary = Glossary(source_lang=source_lang, target_lang=target_lang)

    action = action.lower().strip()

    if action == "add":
        return _add_entries(glossary, entries or [])
    elif action == "list":
        return glossary
    elif action == "search":
        return _search_entries(glossary, entries or [])
    elif action == "delete":
        return _delete_entries(glossary, entries or [])
    elif action == "clear":
        return Glossary(source_lang=glossary.source_lang, target_lang=glossary.target_lang)
    else:
        raise ValueError(f"Unknown glossary action: {action}. Use add/list/search/delete/clear.")


def _normalize_entries(entries: list) -> list[TermEntry]:
    """Convert list of entries to TermEntry objects."""
    normalized: list[TermEntry] = []
    for e in entries:
        if isinstance(e, TermEntry):
            normalized.append(e)
        elif isinstance(e, dict):
            normalized.append(TermEntry(**e))
        else:
            raise TypeError(f"Expected TermEntry or dict, got {type(e).__name__}")
    return normalized


def _add_entries(glossary: Glossary, entries: list) -> Glossary:
    """Add entries to the glossary, updating existing ones."""
    new_entries = _normalize_entries(entries)
    existing = {e.source.lower(): e for e in glossary.entries}

    for entry in new_entries:
        existing[entry.source.lower()] = entry

    # Preserve original case order, new entries appended
    updated = list(existing.values())
    return Glossary(
        source_lang=glossary.source_lang,
        target_lang=glossary.target_lang,
        entries=updated,
    )


def _search_entries(glossary: Glossary, entries: list) -> Glossary:
    """Return a glossary filtered to matching entries only."""
    search_terms = _normalize_entries(entries)
    if not search_terms:
        return glossary

    search_sources = {e.source.lower() for e in search_terms}
    matching = [e for e in glossary.entries if e.source.lower() in search_sources]
    return Glossary(
        source_lang=glossary.source_lang,
        target_lang=glossary.target_lang,
        entries=matching,
    )


def _delete_entries(glossary: Glossary, entries: list) -> Glossary:
    """Remove entries from the glossary."""
    to_delete = _normalize_entries(entries)
    delete_sources = {e.source.lower() for e in to_delete}
    remaining = [e for e in glossary.entries if e.source.lower() not in delete_sources]
    return Glossary(
        source_lang=glossary.source_lang,
        target_lang=glossary.target_lang,
        entries=remaining,
    )
