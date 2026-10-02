PREFIX_TITLES = [
    "prof", "drs", "dra", "dr", "ir", "hj", "h", "kh", "tgk", "ust", "ustadz",
    "st", "sr", "ny", "tn", "mr", "mrs", "ms",
]
SUFFIX_TITLE_PATTERN = r",\s*[a-z][a-z.]{0,14}\.?\s*$"
PATRONYM_PATTERN = r"\s+(bin|binti|ibnu|binte)\s+.*$"
MUHAMMAD_ABBREVIATIONS = ["mochd", "moch", "muhd", "muh", "moh", "mhd", "mch", "m"]
ABBREVIATION_PATTERN = rf"^({'|'.join(MUHAMMAD_ABBREVIATIONS)})\.?\s+"

VARIANTS = {
    "v0": (False, False, False),
    "v1": (True, False, False),
    "v2": (True, False, True),
    "v3": (True, True, False),
    "v4": (False, True, False),
    "v5": (False, False, True),
    "v6": (False, True, True),
    "v7": (True, True, True),
}
LATE_VARIANTS = ("v4", "v5", "v6", "v7")


def prefix_pattern() -> str:
    return rf"^(({'|'.join(PREFIX_TITLES)})\.?\s+)+"


def raw_lower(column: str) -> str:
    return f"lower(trim(CAST({column} AS VARCHAR)))"


def clean_sql(column: str, titles: bool, patronym: bool, abbreviations: bool) -> str:
    raw = raw_lower(column)
    if not (titles or patronym or abbreviations):
        return raw
    x = raw
    if titles:
        x = (f"regexp_replace(regexp_replace({x}, '{SUFFIX_TITLE_PATTERN}', ''), "
             f"'{SUFFIX_TITLE_PATTERN}', '')")
        x = f"regexp_replace({x}, '{prefix_pattern()}', '')"
    if patronym:
        x = f"regexp_replace({x}, '{PATRONYM_PATTERN}', '')"
    if abbreviations:
        x = f"regexp_replace({x}, '{ABBREVIATION_PATTERN}', 'muhammad ')"
    tidy = f"trim(regexp_replace({x}, '\\s+', ' ', 'g'))"
    return f"CASE WHEN nullif({tidy}, '') IS NULL THEN {raw} ELSE {tidy} END"


def full_clean_sql(column: str) -> str:
    return clean_sql(column, True, True, False)


def variant_sql(column: str, variant: str) -> str:
    return clean_sql(column, *VARIANTS[variant])


def has_title_sql(column: str) -> str:
    raw = raw_lower(column)
    return (f"(regexp_matches({raw}, '{prefix_pattern()}') "
            f"OR regexp_matches({raw}, '{SUFFIX_TITLE_PATTERN}'))")


def has_patronym_sql(column: str) -> str:
    return f"regexp_matches({raw_lower(column)}, '{PATRONYM_PATTERN}')"


def variant_for(cleaning: dict | None) -> str:
    c = cleaning or {}
    wanted = (bool(c.get("titles")), bool(c.get("patronym")), bool(c.get("abbreviations")))
    return next(key for key, flags in VARIANTS.items() if flags == wanted)


def variant_columns(prefix: str) -> list[str]:
    return [f"{prefix}_{v}" for v in VARIANTS]


def missing_variant_sql(variant: str) -> str:
    return (f"(name_v0 IS NOT NULL AND name_{variant} IS NULL) "
            f"OR (mother_v0 IS NOT NULL AND mother_{variant} IS NULL)")
