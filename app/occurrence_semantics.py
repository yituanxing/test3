"""Explicit occurrence identities for fictional person scenario data.

A synthetic observation is not necessarily an independent appearance event:
consecutive frame captures can have distinct event_keys but one occurrence_key.
Do NOT use this to associate Frigate real-person tracks or infer biometric
identities across cameras. Missing occurrence_key preserves legacy one-row-one-
event behavior; no time-proximity guessing is performed here.
"""
from __future__ import annotations


def occurrence_identity(row: dict) -> tuple[str, int, str]:
    """Stable *synthetic-only* effective person/place/event key.

    Used for counting distinct simulated appearance events. Returns a tuple
    scoped by effective fictional identity and scene place, making collisions
    across persons/places impossible even with duplicated raw key strings.
    """
    person = row.get('effective_person') or row.get('person') or row.get('person_label')
    if not person:
        raise ValueError('Synthetic occurrence requires a fictional person label')
    place = int(row['place_id'])
    key = row.get('occurrence_key')
    if key:
        return (str(person), place, 'occurrence:' + str(key))
    raw_key = row.get('event_key')
    if not raw_key:
        raw_key = 'legacy-id:' + str(row['id'])
    return (str(person), place, 'observation:' + str(raw_key))


def appearance_count(rows: list[dict]) -> int:
    """Count explicit distinct synthetic events, NOT detected frames or visits."""
    return len({occurrence_identity(row) for row in rows})
