import json
from datetime import datetime, timedelta, timezone


def q(value) -> str:
    """DuckDB literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return "'" + str(value).replace("'", "''") + "'"


def sq(value) -> str:
    """StarRocks literal: backslash is an escape character there."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def sjson(value) -> str:
    if value is None:
        return "NULL"
    return f"parse_json({sq(json.dumps(value, ensure_ascii=False, default=str))})"


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


# Every DATETIME column in StarRocks holds WIB (UTC+7) wall-clock time without a zone. StarRocks'
# own now() follows the cluster zone (UTC on our clusters), so SQL never uses it for stored times.
WIB = timedelta(hours=7)
ZONE = "+07:00"  # offset appended when a stored time leaves the API as ISO 8601


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + WIB


def as_text(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def now_text() -> str:
    return as_text(now())
