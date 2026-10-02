import json
import re
import time
import urllib.error
import urllib.request

from . import settings as cfg


def is_local(url: str) -> bool:
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    return bool(host in ("localhost", "127.0.0.1", "host.docker.internal")
                or re.match(r"^10\.", host) or re.match(r"^192\.168\.", host)
                or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host))


def is_on_prem(url: str) -> bool:
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    return is_local(url) or (bool(host) and "." not in host)


def column_ai_enabled() -> bool:
    return cfg.COLUMN_AI_MODE != "off" and bool(cfg.COLUMN_AI_BASE_URL)


def column_ai_off_reason() -> str:
    if cfg.COLUMN_AI_MODE == "off":
        return "NORMALISASI_AI=off"
    if not cfg.COLUMN_AI_BASE_URL:
        return ("endpoint AI belum dikonfigurasi — setel NORMALISASI_AI_BASE_URL "
                "atau OLLAMA_LOCAL_BASE_URL di environment container")
    return "aktif"


def samples_allowed() -> tuple[bool, str]:
    url = cfg.COLUMN_AI_BASE_URL
    if cfg.COLUMN_AI_MODE != "dengan_sampel":
        return False, f"mode AI '{cfg.COLUMN_AI_MODE}' — contoh nilai tidak dikirim"
    if is_local(url):
        return True, f"endpoint lokal ({url}) — contoh nilai boleh dikirim"
    if cfg.COLUMN_AI_EXTERNAL_SAMPLES:
        return True, (f"endpoint LUAR ({url}) dengan izin eksplisit "
                      "NORMALISASI_AI_IZIN_SAMPEL_LUAR=1")
    return False, (
        f"endpoint {url} BUKAN jaringan lokal. Contoh nilai tidak dikirim "
        "karena akan membawa NIK dan nama keluar jaringan. Arahkan "
        "NORMALISASI_AI_BASE_URL ke Ollama on-prem, atau setel "
        "NORMALISASI_AI_IZIN_SAMPEL_LUAR=1 kalau itu memang disengaja.")


def _chat_url(base: str, append_v1: bool) -> str:
    url = base.rstrip("/")
    if not append_v1:
        return url + "/v1/chat/completions"
    if url.endswith("/chat/completions"):
        return url
    return url + ("/chat/completions" if url.endswith("/v1") else "/v1/chat/completions")


def chat(url: str, api_key: str, model: str, messages: list[dict], timeout: int,
         attempts: int, delay: float) -> dict:
    body = json.dumps({"model": model, "messages": messages, "temperature": 0,
                       "stream": False}).encode()
    error, started = None, time.perf_counter()
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read())
            usage = result.get("usage") or {}
            return {"text": result["choices"][0]["message"]["content"],
                    "seconds": round(time.perf_counter() - started, 2),
                    "tokens": usage.get("total_tokens"), "attempt": attempt}
        except urllib.error.HTTPError as e:
            error = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
        if attempt < attempts:
            time.sleep(delay * attempt)
    raise RuntimeError(f"{attempts} percobaan — {error}")


def ask_columns(prompt: str) -> dict:
    if not cfg.COLUMN_AI_BASE_URL:
        raise RuntimeError("Endpoint AI belum dikonfigurasi. Setel NORMALISASI_AI_BASE_URL "
                           "atau OLLAMA_LOCAL_BASE_URL.")
    try:
        answer = chat(_chat_url(cfg.COLUMN_AI_BASE_URL, False), cfg.COLUMN_AI_API_KEY,
                      cfg.COLUMN_AI_MODEL, [{"role": "user", "content": prompt}],
                      cfg.COLUMN_AI_TIMEOUT, cfg.COLUMN_AI_ATTEMPTS, cfg.COLUMN_AI_RETRY_DELAY)
    except RuntimeError as e:
        raise RuntimeError(f"AI gagal setelah {e}") from e
    return {**answer, "model": cfg.COLUMN_AI_MODEL, "endpoint": cfg.COLUMN_AI_BASE_URL}


def ask_reasoning(system: str, text: str) -> str:
    try:
        answer = chat(_chat_url(cfg.REASONING_AI_BASE_URL, True), cfg.REASONING_AI_API_KEY,
                      cfg.REASONING_AI_MODEL,
                      [{"role": "system", "content": system},
                       {"role": "user", "content": f"Teks:\n{text}"}],
                      cfg.REASONING_AI_TIMEOUT, cfg.REASONING_AI_RETRIES, 1.5)
    except RuntimeError as e:
        raise RuntimeError(f"LLM gagal setelah {e}") from e
    return answer["text"]


def parse_json(text: str) -> dict:
    body = text.strip()
    if body.startswith("```"):
        parts = body.split("```")
        if len(parts) >= 2:
            body = parts[1]
            if body.lower().startswith("json"):
                body = body[4:]
    body = body.strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        raise ValueError(f"Balasan AI tidak memuat JSON: {text[:200]!r}")
    return json.loads(m.group(0))
