# venues.py
def classify(venue_name, venue_key, canonical_list):
    name = venue_name or ""
    for c in canonical_list:
        if venue_key in (c.get("platform_ids") or []):
            return c
    for c in canonical_list:
        if any(p and p in name for p in (c.get("name_patterns") or [])):
            return c
    return None
