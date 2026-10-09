import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid

from fastapi import Header, HTTPException, Query

from . import settings as cfg
from . import sr
from .sql import now_text, now_wib_text, sq

TABLE = f"{cfg.T_SERVICE}service_api_keys"
USER_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synchrono-service/user/{cfg.SUPERUSER}"))
NO_KEY = "An API key must be passed as query or header"
BAD_KEY = "Invalid or missing API key"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(cfg.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def make_token(kind: str, lifetime: int) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"sub": USER_ID, "type": kind, "exp": int(time.time()) + lifetime},
                             separators=(",", ":")).encode())
    return f"{head}.{claims}.{_sign(f'{head}.{claims}')}"


def token_valid(token: str) -> bool:
    try:
        head, claims, signature = token.split(".")
        if not _same(signature, _sign(f"{head}.{claims}")):
            return False
        body = json.loads(_unb64(claims))
        return (body.get("type") == "access" and body.get("sub") == USER_ID
                and body.get("exp", 0) > time.time())
    except Exception:  # noqa: BLE001
        return False


def login(username: str, password: str) -> dict | None:
    if not cfg.SUPERUSER_PASSWORD:
        return None
    if not (_same(username, cfg.SUPERUSER) and _same(password, cfg.SUPERUSER_PASSWORD)):
        return None
    return {"access_token": make_token("access", cfg.ACCESS_TOKEN_SECONDS),
            "refresh_token": make_token("refresh", cfg.REFRESH_TOKEN_SECONDS),
            "token_type": "bearer"}


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _iso(t) -> str | None:
    return None if t is None else t.replace(microsecond=0).isoformat() + "+00:00"


def create_key(name: str | None) -> dict:
    key = f"sk-{secrets.token_urlsafe(32)}"
    key_id = str(uuid.uuid4())
    sr.execute(f"INSERT INTO {TABLE} (id, name, key_hash, key_prefix, key_length, user_id, "
               f"created_at, total_uses, is_active, created_date, created_by) VALUES "
               f"({sq(key_id)}, {sq(name)}, {sq(_hash(key))}, {sq(key[:8])}, {len(key)}, "
               f"{sq(USER_ID)}, {sq(now_text())}, 0, TRUE, {sq(now_wib_text())}, "
               f"{sq(cfg.SUPERUSER)})")
    return {"name": name, "last_used_at": None, "total_uses": 0, "is_active": True,
            "expires_at": None, "id": key_id, "api_key": key, "user_id": USER_ID}


def list_keys() -> dict:
    rows = sr.query(f"SELECT name, last_used_at, total_uses, is_active, expires_at, id, "
                    f"key_prefix, key_length, created_at FROM {TABLE} "
                    f"WHERE user_id = {sq(USER_ID)} ORDER BY created_at")
    keys = [{"name": r["name"], "last_used_at": _iso(r["last_used_at"]),
             "total_uses": int(r["total_uses"] or 0), "is_active": bool(r["is_active"]),
             "expires_at": _iso(r["expires_at"]), "id": r["id"],
             "api_key": r["key_prefix"] + "*" * (r["key_length"] - len(r["key_prefix"])),
             "user_id": USER_ID, "created_at": _iso(r["created_at"])} for r in rows]
    return {"total_count": len(keys), "user_id": USER_ID, "api_keys": keys}


_cache: dict[str, tuple[str | None, float]] = {}
_cache_lock = threading.Lock()
_uses: dict[str, int] = {}
_uses_lock = threading.Lock()


def delete_key(key_id: str) -> bool:
    try:
        uuid.UUID(key_id)
    except ValueError:
        return False
    if not sr.query(f"SELECT 1 FROM {TABLE} WHERE id = {sq(key_id)} "
                    f"AND user_id = {sq(USER_ID)}"):
        return False
    sr.execute(f"DELETE FROM {TABLE} WHERE id = {sq(key_id)}")
    with _cache_lock:
        for h in [h for h, (k, _) in _cache.items() if k == key_id]:
            _cache.pop(h, None)
    return True


def _lookup(h: str) -> str | None:
    rows = sr.query(f"SELECT id FROM {TABLE} WHERE key_hash = {sq(h)} AND is_active "
                    f"AND (expires_at IS NULL OR expires_at > {sq(now_text())})")
    return rows[0]["id"] if rows else None


def check_key(key: str | None) -> None:
    if not cfg.AUTH_ENABLED:
        return
    if not key:
        raise HTTPException(status_code=403, detail=NO_KEY)
    for static in cfg.API_KEYS:
        if _same(key, static):
            return
    h = _hash(key)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(h)
    if cached and cached[1] > now:
        key_id = cached[0]
    else:
        try:
            key_id = _lookup(h)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=(
                f"API key tidak bisa diperiksa — StarRocks tidak terjangkau: "
                f"{type(e).__name__}: {e}")) from e
        with _cache_lock:
            if len(_cache) >= 10_000:
                _cache.clear()
            _cache[h] = (key_id, now + (60 if key_id else 10))
    if not key_id:
        raise HTTPException(status_code=403, detail=BAD_KEY)
    with _uses_lock:
        _uses[key_id] = _uses.get(key_id, 0) + 1


def flush_uses() -> None:
    global _uses
    with _uses_lock:
        data, _uses = _uses, {}
    for key_id, n in data.items():
        sr.execute(f"UPDATE {TABLE} SET total_uses = total_uses + {int(n)}, "
                   f"last_used_at = {sq(now_text())} WHERE id = {sq(key_id)}")


def start_flusher(every: float = 30.0) -> None:
    def loop():
        while True:
            time.sleep(every)
            try:
                flush_uses()
            except Exception as e:  # noqa: BLE001
                print(f"[auth] key usage not written: {type(e).__name__}: {e}", flush=True)
    threading.Thread(target=loop, daemon=True, name="auth-flusher").start()


def require_key(header_key: str | None = Header(default=None, alias="x-api-key"),
                query_key: str | None = Query(default=None, alias="x-api-key")) -> None:
    check_key(query_key or header_key)


def require_key_rest(header_key: str | None = Header(default=None, alias="x-api-key"),
                     query_key: str | None = Query(default=None, alias="x-api-key")) -> None:
    try:
        check_key(query_key or header_key)
    except HTTPException as e:
        if e.status_code == 403:
            raise HTTPException(status_code=401, detail=BAD_KEY) from e
        raise


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=403, detail="No authentication credentials provided")
    if not token_valid(authorization[7:].strip()):
        raise HTTPException(status_code=401, detail="Invalid token")
