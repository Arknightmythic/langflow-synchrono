import hashlib
import re
import time
from collections import Counter

from . import llm, sr
from . import settings as cfg
from .columns import GENDER_FEMALE, GENDER_MALE
from .sql import now_text, sq

MAX_CANDIDATES = 5
EMPTY_TOKENS = ("", "-", "null", "none", "nan", "kosong")
PLACEHOLDER = re.compile(r"\{[a-z0-9_]+(?:\.[a-z0-9_]+)?\}")

ELEMENTS = [
    ("nik", "NIK", "{incoming.nik}", "{master.nik}"),
    ("nama", "nama lengkap", "{incoming.nama}", "{master.nama}"),
    ("tgl", "tanggal lahir", "{incoming.tanggal_lahir}", "{master.tanggal_lahir}"),
    ("jk", "jenis kelamin", "{incoming.jenis_kelamin}", "{master.jenis_kelamin}"),
    ("ibu", "nama ibu kandung", "{incoming.nama_ibu}", "{master.nama_ibu}"),
    ("tmp", "tempat lahir", "{incoming.tempat_lahir}", "{master.tempat_lahir}"),
]
KEYS = [e[0] for e in ELEMENTS]
RAW_I = {"nik": "i_nik", "nama": "i_nama", "tgl": "i_tgl_raw", "jk": "i_jk", "ibu": "i_ibu",
         "tmp": "i_tmp"}
RAW_M = {"nik": "m_nik", "nama": "m_nama", "tgl": "m_tgl", "jk": "m_jk", "ibu": "m_ibu",
         "tmp": "m_tmp"}
CLEAN = {"nama": ("i_name_c", "m_name_c"), "ibu": ("i_mother_c", "m_mother_c"),
         "tmp": ("i_pob_c", "m_pob_c")}


def number_text(expr: str) -> str:
    x = f"CAST({expr} AS DOUBLE)"
    return (f"CASE WHEN {x} IS NULL THEN NULL WHEN {x} = floor({x}) "
            f"AND abs({x}) < 1e15 THEN concat(CAST(CAST({x} AS BIGINT) AS VARCHAR), '.0') "
            f"ELSE CAST({x} AS VARCHAR) END")


def _text(column: str) -> str:
    return f"trim(CAST({column} AS VARCHAR))"


def _iso(column: str) -> str:
    return f"date_format({column}, '%Y-%m-%d')"


VALUES = {
    "{incoming.nik}": _text("i_nik"),
    "{master.nik}": _text("m_nik"),
    "{incoming.nama}": _text("i_nama"),
    "{master.nama}": _text("m_nama"),
    "{incoming.tanggal_lahir}": f"COALESCE({_iso('i_dob')}, {_text('i_tgl_raw')})",
    "{master.tanggal_lahir}": _iso("m_tgl"),
    "{incoming.jenis_kelamin}": _text("i_jk"),
    "{master.jenis_kelamin}": _text("m_jk"),
    "{incoming.nama_ibu}": _text("i_ibu"),
    "{master.nama_ibu}": _text("m_ibu"),
    "{incoming.tempat_lahir}": _text("i_tmp"),
    "{master.tempat_lahir}": _text("m_tmp"),
    "{skor}": number_text("score"),
    "{jw_nama}": number_text("jw_name"),
    "{n_kandidat}": "CAST(n_candidates AS VARCHAR)",
    "{n_seri}": "CAST(n_tie AS VARCHAR)",
}
for _n in range(1, MAX_CANDIDATES + 1):
    VALUES |= {
        f"{{kandidat{_n}.nik}}": _text(f"k{_n}_nik"),
        f"{{kandidat{_n}.nama}}": _text(f"k{_n}_name"),
        f"{{kandidat{_n}.tempat_lahir}}": _text(f"k{_n}_pob"),
        f"{{kandidat{_n}.tanggal_lahir}}": _iso(f"k{_n}_dob"),
        f"{{kandidat{_n}.skor}}": number_text(f"k{_n}_score"),
    }


def empty_sql(column: str) -> str:
    tokens = ", ".join(sq(t) for t in EMPTY_TOKENS)
    return f"(COALESCE(lower(trim(CAST({column} AS VARCHAR))), '') IN ({tokens}))"


def _gender(column: str) -> str:
    value = f"lower(trim(CAST({column} AS VARCHAR)))"
    male = ", ".join(sq(v) for v in GENDER_MALE)
    female = ", ".join(sq(v) for v in GENDER_FEMALE)
    return f"(CASE WHEN {value} IN ({male}) THEN 'l' WHEN {value} IN ({female}) THEN 'p' ELSE {value} END)"


def _master_verdict(f: str) -> str:
    empty_i, empty_m = empty_sql(RAW_I[f]), empty_sql(RAW_M[f])
    if f == "nik":
        return (f"CASE WHEN {empty_i} THEN 'EMPTY_IN_INSTITUTION' WHEN i_nik = m_nik THEN 'SAME' "
                f"WHEN NOT COALESCE(i_nik_trusted, FALSE) THEN 'UNTRUSTED' "
                f"WHEN nik_in_master THEN 'OWNED_BY_OTHER' ELSE 'NOT_IN_MASTER' END")
    if f == "tgl":
        return (f"CASE WHEN {empty_i} THEN 'EMPTY_IN_INSTITUTION' "
                f"WHEN i_dob IS NULL THEN 'UNREADABLE_IN_INSTITUTION' "
                f"WHEN {empty_m} THEN 'EMPTY_IN_MASTER' WHEN i_dob = m_tgl THEN 'SAME' "
                f"ELSE 'DIFFERENT' END")
    same = (f"{_gender('i_jk')} = {_gender('m_jk')}" if f == "jk"
            else f"{CLEAN[f][0]} = {CLEAN[f][1]}")
    cleaned = "WHEN i_name_full = m_name_full THEN 'SAME_CLEANED' " if f == "nama" else ""
    return (f"CASE WHEN {empty_i} THEN 'EMPTY_IN_INSTITUTION' WHEN {empty_m} THEN 'EMPTY_IN_MASTER' "
            f"WHEN {same} THEN 'SAME' {cleaned}ELSE 'DIFFERENT' END")


def _incoming_state(f: str) -> str:
    empty_i = empty_sql(RAW_I[f])
    if f == "nik":
        return (f"CASE WHEN {empty_i} THEN 'EMPTY_IN_INSTITUTION' "
                f"WHEN NOT COALESCE(i_nik_trusted, FALSE) THEN 'UNTRUSTED' "
                f"WHEN nik_in_master THEN 'IN_MASTER' ELSE 'NOT_IN_MASTER' END")
    if f == "tgl":
        return (f"CASE WHEN {empty_i} THEN 'EMPTY_IN_INSTITUTION' "
                f"WHEN i_dob IS NULL THEN 'UNREADABLE_IN_INSTITUTION' ELSE 'PRESENT' END")
    return f"CASE WHEN {empty_i} THEN 'EMPTY_IN_INSTITUTION' ELSE 'PRESENT' END"


def present_sql() -> str:
    return ", ".join(f"max(CASE WHEN NOT {empty_sql(RAW_I[f])} THEN 1 ELSE 0 END) AS {f}"
                     for f in KEYS)


def signature_sql(present: dict[str, bool]) -> str:
    def chain(verdict) -> str:
        return ", ".join(f"concat('{f}=', {verdict(f) if present[f] else sq('NA')})" for f in KEYS)

    shown = " + ".join(f"(CASE WHEN k{n}_nik IS NULL THEN 0 ELSE 1 END)"
                       for n in range(1, MAX_CANDIDATES + 1))
    return f"""CASE
        WHEN status = 'UNMATCH' THEN concat_ws('|', 'status=UNMATCH',
            concat('sebab=', CASE WHEN n_candidates = 0 THEN 'NO_CANDIDATE' ELSE 'SCORED' END),
            {chain(_incoming_state)})
        WHEN status = 'CONFLICT' THEN concat_ws('|', 'status=CONFLICT', concat('metode=', method),
            concat('tampil=', CAST(({shown}) AS VARCHAR)),
            concat('lebih=', CASE WHEN n_tie > ({shown}) THEN 'YA' ELSE 'TIDAK' END))
        ELSE concat_ws('|', concat('status=', status), concat('metode=', method),
            concat('pola=', COALESCE(pattern_group, '-')), {chain(_master_verdict)})
    END"""


def _list(xs: list[str]) -> str:
    if len(xs) == 1:
        return xs[0]
    if len(xs) == 2:
        return f"{xs[0]} dan {xs[1]}"
    return ", ".join(xs[:-1]) + f", dan {xs[-1]}"


def _capital(s: str) -> str:
    return s[:1].upper() + s[1:]


NIK_VS_MASTER = {
    "UNTRUSTED": "NIK berkas ({incoming.nik}) berbeda dengan NIK master dan sudah "
                 "ditandai tidak tepercaya saat grading",
    "NOT_IN_MASTER": "NIK berkas ({incoming.nik}) tidak terdaftar di master",
    "OWNED_BY_OTHER": "Perhatian: NIK berkas ({incoming.nik}) terdaftar di master "
                      "atas nama orang lain",
}
NIK_UNMATCH = {
    "UNTRUSTED": "NIK berkas ({incoming.nik}) ditandai tidak tepercaya saat grading",
    "NOT_IN_MASTER": "NIK berkas ({incoming.nik}) tidak terdaftar di master",
    "IN_MASTER": "NIK berkas ({incoming.nik}) terdaftar di master, tetapi data identitas "
                 "lainnya tidak cukup cocok dengan pemilik NIK tersebut",
}
DATE_UNREADABLE = ("tanggal lahir pada data incoming ('{incoming.tanggal_lahir}') "
                   "tidak dapat dibaca sebagai tanggal")
TITLE_DIFFERENCE = "gelar akademis/keagamaan atau bin/binti"


def _different_clause(key: str, label: str, ph_i: str, ph_m: str) -> str:
    if key == "nama":
        return f"nama lengkap berbeda ('{ph_i}' vs '{ph_m}', Jaro-Winkler {{jw_nama}}%)"
    if key == "tgl":
        return f"tanggal lahir berbeda ({ph_i} vs {ph_m})"
    return f"{label} berbeda ('{ph_i}' vs '{ph_m}')"


def _details(s: dict, skip: set[str], also: bool) -> list[str]:
    notes, different, same, empty_i, empty_m = [], [], [], [], []
    for key, label, ph_i, ph_m in ELEMENTS:
        v = s.get(key, "NA")
        if key in skip or v == "NA":
            continue
        if v == "SAME":
            same.append(label)
        elif v == "DIFFERENT":
            different.append(_different_clause(key, label, ph_i, ph_m))
        elif v == "SAME_CLEANED":
            different.append(f"nama lengkap hanya berbeda pada {TITLE_DIFFERENCE} "
                             f"('{ph_i}' vs '{ph_m}')")
        elif v == "EMPTY_IN_INSTITUTION":
            empty_i.append(label)
        elif v == "EMPTY_IN_MASTER":
            empty_m.append(label)
        elif v == "UNREADABLE_IN_INSTITUTION":
            notes.append(DATE_UNREADABLE)
        elif key == "nik" and v in NIK_VS_MASTER:
            notes.append(NIK_VS_MASTER[v])
    sentences = [f"{n}." for n in notes]
    if different:
        sentences.append(f"{_list(different)}.")
    if same:
        sentences.append(f"{_list(same)} {'juga ' if also else ''}identik.")
    if empty_i:
        sentences.append(f"{_list(empty_i)} kosong pada data incoming.")
    if empty_m:
        sentences.append(f"{_list(empty_m)} kosong pada master.")
    return [_capital(x) for x in sentences]


def _template_match(s: dict) -> str:
    status, method, pattern = s["status"], s["metode"], s["pola"]
    also = False
    if status == "AUTO" and method == "PASS1_NIK_NAMA":
        head = ("Cocok otomatis melalui pencocokan deterministik Pass 1: NIK "
                "({master.nik}) dan nama lengkap identik dengan master.")
        skip, also = {"nik", "nama"}, True
    elif status == "AUTO" and method == "PASS2_NAMA_TGL_IBU":
        head = ("Cocok otomatis melalui pencocokan deterministik Pass 2: nama lengkap, "
                "tanggal lahir ({master.tanggal_lahir}), dan nama ibu kandung identik "
                "dengan master NIK {master.nik}.")
        skip, also = {"nama", "tgl", "ibu"}, True
    elif status == "AUTO":
        head = ("Cocok otomatis melalui pencocokan skor (Pass 3): skor kemiripan "
                "{skor}% terhadap master NIK {master.nik}.")
        skip = set()
    elif pattern == "NIK_CONFLICT" and s.get("nik") == "OWNED_BY_OTHER":
        head = ("Peringatan: nama lengkap, tanggal lahir, dan nama ibu kandung identik "
                "dengan master NIK {master.nik}, tetapi NIK berkas ({incoming.nik}) "
                "terdaftar di master atas nama orang lain. Disarankan verifikasi fisik "
                "dokumen.")
        skip, also = {"nik", "nama", "tgl", "ibu"}, True
    elif pattern == "NIK_CONFLICT" and s.get("nik") == "SAME":
        head = ("Peringatan: NIK cocok dengan master ({master.nik}), namun nama warga "
                "('{incoming.nama}') berbeda total dengan master ('{master.nama}', "
                "Jaro-Winkler {jw_nama}%). Disarankan verifikasi fisik dokumen.")
        skip = {"nik", "nama"}
    elif pattern == "TITLE_DEGREE":
        head = (f"Skor kemiripan {{skor}}%. Nama hanya berbeda pada {TITLE_DIFFERENCE} "
                "('{incoming.nama}' vs '{master.nama}').")
        skip = {"nama"}
    elif pattern == "SWAPPED_DOB":
        head = ("Skor kemiripan {skor}%. Hari dan bulan lahir terindikasi tertukar "
                "({incoming.tanggal_lahir} pada data incoming vs {master.tanggal_lahir} "
                "pada master).")
        skip = {"tgl"}
    elif pattern == "SPELLING_NAME":
        head = ("Skor kemiripan {skor}%. Terdapat perbedaan ejaan nama ('{incoming.nama}' "
                "vs '{master.nama}', Jaro-Winkler {jw_nama}%).")
        skip = {"nama"}
    else:
        head = ("Skor kemiripan {skor}% terhadap master NIK {master.nik} belum memenuhi "
                "syarat pencocokan otomatis.")
        skip = set()
    return " ".join([head] + _details(s, skip, also))


def _template_conflict(s: dict) -> str:
    scored = s["metode"] == "SCORING"

    def describe(n: int) -> str:
        k = f"kandidat{n}"
        body = f"{{{k}.nama}}, tempat lahir {{{k}.tempat_lahir}}"
        if scored:
            body += f", tanggal lahir {{{k}.tanggal_lahir}}, skor {{{k}.skor}}%"
        return f"Kandidat {n} NIK {{{k}.nik}} ({body})"

    if scored:
        head = "Sebanyak {n_seri} kandidat teratas memiliki skor seimbang"
    else:
        criteria = ("NIK dan nama lengkap" if s["metode"] == "PASS1_NIK_NAMA"
                    else "nama lengkap, tanggal lahir, dan nama ibu kandung")
        head = f"Ditemukan {{n_seri}} kandidat master dengan {criteria} identik"
    shown = int(s.get("tampil", "0"))
    if shown >= 2:
        head += f"; {shown} di antaranya" if s.get("lebih") == "YA" else ""
        head += ": " + _list([describe(n) for n in range(1, shown + 1)])
    return head + ". Sistem tidak memilih salah satunya secara otomatis."


def _template_unmatch(s: dict) -> str:
    if s["sebab"] == "NO_CANDIDATE":
        head = ("Tidak ditemukan catatan kependudukan yang relevan pada Master Data "
                "Dukcapil: tidak ada kandidat yang lolos penyaringan awal (blocking).")
    else:
        head = ("Tidak ditemukan catatan kependudukan yang cukup mirip pada Master Data "
                "Dukcapil: kandidat terdekat memperoleh skor {skor}% dan tidak memenuhi "
                "kriteria pencocokan otomatis maupun tinjauan.")
    sentences = [head]
    if s.get("nik") in NIK_UNMATCH:
        sentences.append(f"{NIK_UNMATCH[s['nik']]}.")
    empty = [label for key, label, _, _ in ELEMENTS if s.get(key) == "EMPTY_IN_INSTITUTION"]
    if empty:
        sentences.append(_capital(f"{_list(empty)} kosong pada data incoming."))
    if s.get("tgl") == "UNREADABLE_IN_INSTITUTION":
        sentences.append(_capital(f"{DATE_UNREADABLE}."))
    return " ".join(sentences)


def template(signature: str) -> str:
    s = dict(part.split("=", 1) for part in signature.split("|"))
    if s["status"] == "UNMATCH":
        return _template_unmatch(s)
    if s["status"] == "CONFLICT":
        return _template_conflict(s)
    return _template_match(s)


def hydrate_sql(text: str) -> str:
    parts, start = [], 0
    for m in PLACEHOLDER.finditer(text):
        if m.start() > start:
            parts.append(sq(text[start:m.start()]))
        parts.append(f"COALESCE({VALUES[m.group()]}, '(kosong)')")
        start = m.end()
    if start < len(text):
        parts.append(sq(text[start:]))
    return f"concat({', '.join(parts)})"


def reasoning_sql(templates: dict[str, str]) -> str:
    branches = " ".join(f"WHEN {sq(sig)} THEN {hydrate_sql(text)}"
                        for sig, text in templates.items())
    return f"CASE signature {branches} END" if branches else "CAST(NULL AS VARCHAR)"


VERSION = "id-2"
PATTERN_TABLE = f"{cfg.T_SERVICE}reasoning_patterns"
PROMPT = """Anda adalah penyunting bahasa untuk penjelasan hasil pencocokan data kependudukan. Anda menerima satu teks penjelasan yang isinya sudah BENAR. Tugas Anda hanya memperhalus bahasanya agar enak dibaca operator.

ATURAN WAJIB:
1. Setiap placeholder berkurung kurawal — misalnya {incoming.nama}, {master.nik}, {skor} — harus muncul PERSIS seperti aslinya dan sebanyak aslinya. Jangan menerjemahkan, mengubah, menggabungkan, atau menghapus placeholder.
2. Jangan menambah fakta, angka, nama, atau placeholder baru. Jangan menghilangkan informasi apa pun.
3. Jangan membalik makna: yang identik tetap identik, yang berbeda tetap berbeda, yang kosong tetap kosong.
4. Bahasa Indonesia baku dan ringkas, paling banyak tiga kalimat.
5. Balas HANYA dengan teks hasilnya — tanpa tanda kutip pembungkus, tanpa judul, tanpa penjelasan."""
KEYWORDS = ("identik", "beda", "kosong", "tidak", "tertukar", "gelar", "peringatan", "perhatian",
            "seimbang", "tepercaya", "terdaftar", "otomatis", "ejaan", "kandidat")


def check(base: str, answer: str) -> tuple[str | None, str]:
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", (answer or "").strip())
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None, "jawaban kosong"
    if Counter(PLACEHOLDER.findall(text)) != Counter(PLACEHOLDER.findall(base)):
        return None, "placeholder tidak utuh"
    outside, outside_base = PLACEHOLDER.sub("", text), PLACEHOLDER.sub("", base)
    if re.search(r"[{}*#`]", outside):
        return None, "karakter terlarang di luar placeholder"
    if Counter(re.findall(r"\d+", outside)) != Counter(re.findall(r"\d+", outside_base)):
        return None, "angka bertambah atau hilang"
    for word in KEYWORDS:
        if (word in outside.lower()) != (word in outside_base.lower()):
            return None, f"kata kunci '{word}' berubah"
    if not 0.6 * len(base) <= len(text) <= 1.6 * len(base):
        return None, "panjangnya menyimpang"
    return text, "ok"


def cache_key(base: str) -> str:
    return hashlib.md5(f"{VERSION}|{cfg.REASONING_AI_MODEL}|{base}".encode()).hexdigest()


def refine(job: dict, patterns: list[tuple], base: dict, use: dict) -> Counter:
    stats = Counter()
    url = cfg.REASONING_AI_BASE_URL
    if not llm.is_on_prem(url):
        if not cfg.REASONING_AI_ALLOW_EXTERNAL:
            print(f"[R] REASONING_AI_BASE_URL {url} bukan alamat on-prem — LLM tidak dipanggil. "
                  f"Yang dikirim ke LLM memang hanya kalimat berplaceholder, tapi batas jaringan "
                  f"tetap dijaga; setel REASONING_AI_ALLOW_EXTERNAL=1 kalau memang disengaja.",
                  flush=True)
            stats["endpoint_ditolak"] = 1
            return stats
        print(f"[R] external endpoint {url} allowed by REASONING_AI_ALLOW_EXTERNAL=1 — only "
              f"placeholder sentences are sent", flush=True)
    keys = {sig: cache_key(base[sig]) for sig, _, _ in patterns}
    try:
        stored = {r["pattern_hash"]: r["reason_template"] for r in sr.query(
            f"SELECT pattern_hash, reason_template FROM {PATTERN_TABLE} WHERE pattern_hash IN "
            f"({', '.join(sq(h) for h in set(keys.values()))})")}
    except Exception as e:  # noqa: BLE001
        print(f"[R] cache {PATTERN_TABLE} unreadable ({type(e).__name__}: {e}) — LLM skipped, "
              f"all sentences deterministic", flush=True)
        stats["cache_gagal"] = 1
        return stats

    started, dead, fresh, hits = time.perf_counter(), False, [], Counter()
    for sig, n, sample in patterns:
        h = keys[sig]
        if h in stored:
            use[sig] = stored[h]
            hits[h] += n
            stats["cache"] += 1
            continue
        if (dead or stats["llm_dipanggil"] >= cfg.REASONING_AI_MAX_PATTERNS
                or time.perf_counter() - started > cfg.REASONING_AI_BUDGET_SECONDS):
            stats["terlewat"] += 1
            continue
        stats["llm_dipanggil"] += 1
        t = time.perf_counter()
        try:
            answer = llm.ask_reasoning(PROMPT, base[sig])
        except Exception as e:  # noqa: BLE001
            print(f"[R] {e} — remaining patterns use deterministic sentences", flush=True)
            stats["llm_gagal"] += 1
            dead = True
            continue
        finally:
            stats["llm_ms"] += int((time.perf_counter() - t) * 1000)
        result, reason = check(base[sig], answer)
        if result:
            stats["llm_diterima"] += 1
            use[sig] = result
        else:
            stats["llm_ditolak"] += 1
            print(f"[R] LLM answer rejected ({reason}) for pattern {sig}", flush=True)
        source = "llm" if result else "llm-ditolak"
        parts = dict(b.split("=", 1) for b in sig.split("|"))
        short = "/".join(parts.get(k, "-") for k in ("status", "metode", "pola"))
        fresh.append((h, f"{source}:{short}:{h[:8]}"[:255], sig, use[sig],
                      f"{job.get('job_id')}:{sample}", n))

    stamp = sq(now_text())
    for h, name, sig, text, sample, n in fresh:
        try:
            sr.execute(f"INSERT INTO {PATTERN_TABLE} VALUES ({sq(h)}, {sq(name)}, {sq(sig)}, "
                       f"{sq(text)}, {sq(sample)}, {n}, {stamp}, {stamp})")
        except Exception as e:  # noqa: BLE001
            print(f"[R] pattern {h[:8]} not cached: {type(e).__name__}: {e}", flush=True)
    for h, n in hits.items():
        try:
            sr.execute(f"UPDATE {PATTERN_TABLE} SET hit_count = hit_count + {n}, "
                       f"updated_at = {stamp} WHERE pattern_hash = {sq(h)}")
        except Exception as e:  # noqa: BLE001
            print(f"[R] hit_count {h[:8]} not updated: {type(e).__name__}: {e}", flush=True)
    return stats
