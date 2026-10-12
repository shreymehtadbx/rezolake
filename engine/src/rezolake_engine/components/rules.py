"""Entity-kind matching rules shared by the matchers (no model dependencies)."""

from __future__ import annotations

PREAMBLE = (
    "You are an entity resolution expert. Determine which candidate records "
    "(if any) refer to the SAME real-world entity as the input record."
)

RULES = {
    "people": """RULES — apply these in order:
1. NAME is the PRIMARY identifier. If first name AND last name are completely
   different, the records are different entities — regardless of shared address,
   demographics, or any other fields.
2. Acceptable name variations: typos, abbreviations, nicknames (e.g. Bob/Robert),
   or minor spelling differences.
3. A different last name MAY indicate a maiden/married name change, but ONLY if
   other identifying fields (address, phone, ID numbers) are consistent. If both
   the last name AND the address/phone differ, it is almost certainly a different
   person.
4. Records with the same name but different addresses CAN be the same person
   (people move). Weigh the full name match heavily in this case.
5. Shared address, age group, race, or other demographic fields alone do NOT
   constitute a match — many different people share these.
6. When in doubt, do NOT match. False negatives are preferable to false positives.""",
    "companies": """RULES — apply these in order:
1. LEGAL NAME is the PRIMARY identifier. Compare the full business/company name,
   ignoring common suffixes (Inc, LLC, Ltd, Corp, GmbH, SA, etc.).
2. Acceptable name variations: abbreviations (Intl/International), DBA names,
   acronyms, parent-subsidiary relationships with the same brand.
3. Two companies with very similar names but in DIFFERENT industries or
   DIFFERENT jurisdictions (state/country) may still be different entities —
   check registration numbers, tax IDs, or addresses if available.
4. Shared address alone does NOT constitute a match — multiple businesses can
   operate from the same location (co-working spaces, office buildings).
5. Phone numbers, websites, and registration/tax IDs are strong secondary
   identifiers. A matching tax ID or registration number is highly indicative.
6. When in doubt, do NOT match. False negatives are preferable to false positives.""",
    "products": """RULES — apply these in order:
1. PRODUCT NAME + MANUFACTURER/BRAND is the PRIMARY identifier. Compare both
   together; the same product name from different manufacturers is a different
   entity.
2. Acceptable name variations: model number formatting, regional naming, color/
   size suffixes that describe the same SKU.
3. Version or generation differences (v1 vs v2, Gen 3 vs Gen 4) typically
   indicate DIFFERENT entities unless they share an exact SKU or UPC.
4. Matching SKU, UPC, EAN, ASIN, or other standard identifiers is a very
   strong signal — treat as a match unless other fields clearly contradict.
5. Category, price range, and description are supporting fields — they can
   confirm a match but are NOT sufficient on their own.
6. When in doubt, do NOT match. False negatives are preferable to false positives.""",
    "other": """RULES — apply these in order:
1. Identify the most distinctive attributes in the records (names, IDs,
   codes, titles) and use them as PRIMARY identifiers.
2. Acceptable variations: abbreviations, alternative spellings, formatting
   differences, and locale-specific representations of the same value.
3. Shared generic attributes (dates, categories, status codes) alone do NOT
   constitute a match — many different entities share these.
4. Unique identifiers (serial numbers, reference codes, URIs) are strong
   secondary signals. A matching unique ID is highly indicative.
5. Consider the overall attribute profile: the more independent attributes
   that agree, the stronger the match signal.
6. When in doubt, do NOT match. False negatives are preferable to false positives.""",
}


def rules_for(entity_kind: str) -> str:
    return RULES.get(entity_kind, RULES["other"])
