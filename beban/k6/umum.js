// Fondasi bersama untuk semua skenario k6.
//
// SATU BERKAS SKENARIO, DUA SASARAN
//
// Tiap skenario ditulis sekali dan dijalankan dua kali — `TARGET=service` lalu
// `TARGET=langflow`. Kalau masing-masing punya skripnya sendiri, perbedaan
// kecil yang tidak disengaja (jeda berbeda, jumlah check berbeda, pemeriksaan
// yang lebih longgar di satu sisi) akan menyelinap masuk, dan itu justru jenis
// kesalahan yang paling sulit terlihat di grafik.
//
// YANG DIPERIKSA BUKAN CUMA KODE STATUS
//
// Ini penting khusus untuk Langflow: ia MEMBALAS 200 UNTUK APA PUN, termasuk
// untuk eksekusi yang gagal. Skenario yang hanya memeriksa `status === 200`
// akan melaporkan Langflow 100% sehat sambil sebenarnya tidak ada satu pun
// permintaan yang berhasil — dan angka latency-nya jadi angka latency
// kegagalan, yang selalu tampak bagus. Karena itu tiap check di sini
// MENGURAI BADAN BALASAN dan memastikan field yang diharapkan benar-benar ada.

import http from 'k6/http';
import { check } from 'k6';

export const TARGET = (__ENV.TARGET || 'service').toLowerCase();

const ALAMAT = {
  service: __ENV.SERVICE_URL || 'http://synchrono-service:8000',
  langflow: __ENV.LANGFLOW_URL || 'http://synchrono-langflow:7860',
};

export const BASE = ALAMAT[TARGET];
if (!BASE) throw new Error(`TARGET tidak dikenal: ${TARGET}`);

const KUNCI_SERVICE = __ENV.SERVICE_API_KEY || 'synchrono-bench-key';
const KUNCI_LANGFLOW = __ENV.LANGFLOW_API_KEY || '';

if (TARGET === 'langflow' && !KUNCI_LANGFLOW) {
  throw new Error(
    'LANGFLOW_API_KEY kosong. Kunci Langflow tidak bisa dibaca ulang setelah ' +
    'dibuat, jadi jalankan.ps1 yang membuatkannya — lihat PERBANDINGAN.md.'
  );
}

// Node id flow grading & config bersifat TETAP: diturunkan dari (nama
// endpoint, nama komponen), jadi membangun ulang flow tidak mengubahnya.
// Matching adalah pengecualian — node id-nya masih acak — dan karena itu tidak
// ikut dibebani di sini.
const NODE = {
  dispatch: 'GradingDispatch-a3967',
  status: 'GradingStatus-3cc03',
  rules: 'GradingRuleGet-9c9c5',
};

// Berkas yang dipolling. Beberapa, bukan satu, supaya bukan satu baris
// PostgreSQL yang sama yang dibaca berulang-ulang — cache di sisi basis data
// akan membuat hasilnya terlalu bagus untuk dipercaya.
export const BERKAS = (__ENV.BERKAS || 'uji-a,uji-b,uji-c,uji-d,uji-e')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

export const BUCKET = __ENV.BUCKET || 'syncrono-uploads';

function header() {
  return {
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': TARGET === 'service' ? KUNCI_SERVICE : KUNCI_LANGFLOW,
    },
  };
}

// Selubung Langflow: isi yang berguna terkubur di
// outputs[0].outputs[0].results.message.text, dan isinya masih STRING yang
// perlu diurai sekali lagi.
function kupas(res) {
  // Seluruh 2xx, bukan hanya 200. Service membalas 202 untuk dispatch, dan
  // menolaknya di sini membuat pemeriksaan `jobId` SELALU gagal di sisi
  // service padahal balasannya benar — kegagalan yang berasal dari perkakas
  // uji, bukan dari yang diuji.
  if (res.status < 200 || res.status >= 300) return null;
  try {
    const luar = res.json();
    if (TARGET === 'service') return luar;
    return JSON.parse(luar.outputs[0].outputs[0].results.message.text);
  } catch (e) {
    return null;
  }
}

function lewatFlow(endpoint, node, param, nama) {
  const badan = JSON.stringify({
    output_type: 'chat',
    input_type: 'text',
    input_value: '',
    tweaks: { [node]: param },
  });
  return http.post(`${BASE}/api/v1/run/${endpoint}?stream=false`, badan, {
    ...header(),
    tags: { name: nama },
  });
}

// ── Operasi ────────────────────────────────────────────────────────────────
//
// Tiap fungsi mengembalikan responsnya dan sekaligus mencatat check-nya, jadi
// skenario tinggal memanggil dan tidak bisa lupa memeriksa.

export function periksaKesehatan() {
  // Langflow tidak punya padanan /health yang persis; /health_check adalah yang
  // terdekat dan sama-sama tidak menyentuh basis data.
  const url = TARGET === 'service' ? `${BASE}/health` : `${BASE}/health_check`;
  const res = http.get(url, { tags: { name: 'health' } });
  check(res, { 'health 200': (r) => r.status === 200 });
  return res;
}

export function bacaAturan() {
  let res;
  if (TARGET === 'service') {
    res = http.get(`${BASE}/api/v1/config/rules`, {
      ...header(),
      tags: { name: 'config-rules' },
    });
  } else {
    res = lewatFlow('config-rules', NODE.rules, { grade_id: '' }, 'config-rules');
  }
  const isi = kupas(res);
  check(res, {
    'config 200': (r) => r.status === 200,
    'config berisi 6 grade': () => !!isi && (isi.grades || []).length === 6,
  });
  return res;
}

export function bacaStatus(fileId) {
  let res;
  if (TARGET === 'service') {
    // `panen=false` melewatkan pemanenan job mangkrak, yang isinya satu UPDATE
    // ke PostgreSQL. Node status Langflow SELALU memanennya dan tidak bisa
    // dimatikan, jadi bawaannya di sini pun menyala — mematikannya sepihak
    // berarti membandingkan satu baca terhadap satu baca plus satu tulis.
    const panen = __ENV.PANEN === '0' ? 'false' : 'true';
    res = http.get(`${BASE}/api/v1/grading/jobs/${fileId}?panen=${panen}`, {
      ...header(),
      tags: { name: 'grading-status' },
    });
  } else {
    res = lewatFlow('grading-status', NODE.status,
      { file_id: fileId, job_id: '' }, 'grading-status');
  }
  const isi = kupas(res);
  check(res, {
    'status 200': (r) => r.status === 200,
    'status punya field found': () => !!isi && 'found' in isi,
  });
  return res;
}

export function kirimJob(fileId) {
  let res;
  if (TARGET === 'service') {
    res = http.post(
      `${BASE}/api/v1/grading/jobs`,
      JSON.stringify({
        fileId,
        s3Bucket: BUCKET,
        parquetKey: `uploads/${fileId}/data.parquet`,
      }),
      { ...header(), tags: { name: 'grading-dispatch' } }
    );
  } else {
    res = lewatFlow('grading-dispatch', NODE.dispatch, {
      file_id: fileId,
      s3_bucket: BUCKET,
      parquet_key: `uploads/${fileId}/data.parquet`,
      s3_endpoint: '',
      callback_url: '',
      callback_token: '',
    }, 'grading-dispatch');
  }
  const isi = kupas(res);
  check(res, {
    // 202 di service, 200 di Langflow — Langflow tidak punya kode lain.
    'dispatch diterima': (r) => r.status === 200 || r.status === 202,
    'dispatch mengembalikan jobId': () => !!isi && !!isi.jobId,
  });
  return res;
}

// Status yang SUDAH DIURAI, bukan objek respons.
//
// Dipakai skenario skala: ia harus tahu kapan `done` bernilai true untuk
// berhenti melakukan polling, dan berapa `gradingDurationMs` yang dilaporkan
// engine. Selubung Langflow membuat itu tidak bisa dibaca langsung dari
// respons, jadi pengupasannya dipusatkan di sini.
export function statusTerurai(fileId) {
  const res = bacaStatus(fileId);
  return kupas(res);
}

export function berkasAcak() {
  return BERKAS[Math.floor(Math.random() * BERKAS.length)];
}

// ── Bentuk beban ───────────────────────────────────────────────────────────
//
// ARRIVAL RATE, BUKAN JUMLAH VU. Bedanya menentukan apakah hasilnya bisa
// dibaca sama sekali.
//
// Dengan jumlah VU tetap, tiap VU menunggu balasan sebelum mengirim lagi —
// jadi platform yang LEBIH LAMBAT otomatis MENERIMA LEBIH SEDIKIT permintaan.
// Keduanya lalu tampak sama-sama sanggup, dan yang sesungguhnya berbeda
// (berapa banyak yang bisa dilayani per detik) justru tidak terukur.
//
// Dengan arrival rate, kedua sisi menerima laju permintaan yang SAMA PERSIS,
// dan yang tidak sanggup akan terlihat dari latency yang memanjang dan dari
// peringatan k6 bahwa VU-nya tidak cukup.

export function lajuTetap(rps, detik, nama) {
  return {
    [nama]: {
      executor: 'constant-arrival-rate',
      rate: rps,
      timeUnit: '1s',
      duration: `${detik}s`,
      preAllocatedVUs: Math.max(10, rps * 2),
      // Langflow bisa jauh lebih lambat; tanpa ruang tambahan k6 kehabisan VU
      // dan melaporkan "dropped iterations" alih-alih latency sesungguhnya.
      maxVUs: Math.max(50, rps * 20),
      tags: { skenario: nama },
    },
  };
}

export function lajuNaik(dari, sampai, detikPerTahap, nama) {
  const tahap = [];
  const langkah = 5;
  for (let i = 1; i <= langkah; i++) {
    tahap.push({
      target: Math.round(dari + ((sampai - dari) * i) / langkah),
      duration: `${detikPerTahap}s`,
    });
  }
  return {
    [nama]: {
      executor: 'ramping-arrival-rate',
      startRate: dari,
      timeUnit: '1s',
      stages: tahap,
      preAllocatedVUs: 50,
      maxVUs: 1000,
      tags: { skenario: nama },
    },
  };
}

// Label `target` inilah yang membuat kedua jalan bisa ditampilkan berdampingan
// di satu grafik Grafana. Tanpa ini, jalan kedua hanya menimpa yang pertama.
export const TAG_UMUM = { target: TARGET };

// p99 TIDAK ADA di bawaan k6 (avg, min, med, max, p90, p95), dan ketiadaannya
// tidak menimbulkan galat apa pun — ia hanya muncul sebagai 0,0 ms di
// ringkasan, yang mudah sekali dibaca sebagai "sangat cepat". Padahal justru
// p99-lah yang memperlihatkan permintaan yang tertahan antrean.
export const RINGKAS_TREND = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];

// ── Ringkasan akhir ────────────────────────────────────────────────────────
//
// Ditulis sendiri, tidak memakai jslib.k6.io/k6-summary. Pustaka itu diunduh
// dari internet saat skrip dimuat, dan sebuah benchmark yang gagal jalan gara-
// gara jaringan kantor sedang sibuk adalah benchmark yang tidak akan dipakai
// orang. Lagipula yang dibutuhkan hanya enam angka.

function angka(m, jalan) {
  const v = (m && m.values) || {};
  return jalan.map((k) => v[k]);
}

export function ringkasan(data) {
  const d = data.metrics.http_req_duration;
  const r = data.metrics.http_reqs;
  const c = data.metrics.checks;
  const j = data.metrics.dropped_iterations;

  const [avg, p95, p99, maks] = angka(d, ['avg', 'p(95)', 'p(99)', 'max']);
  const lulus = c ? c.values.passes : 0;
  const gagal = c ? c.values.fails : 0;

  const baris = [
    '',
    `======  ${TARGET.toUpperCase()}  ${'='.repeat(46 - TARGET.length)}`,
    `  permintaan      : ${r ? r.values.count : 0} (${(r ? r.values.rate : 0).toFixed(1)}/detik)`,
    `  latency  avg    : ${(avg || 0).toFixed(1)} ms`,
    `           p95    : ${(p95 || 0).toFixed(1)} ms`,
    `           p99    : ${(p99 || 0).toFixed(1)} ms`,
    `           maks   : ${(maks || 0).toFixed(1)} ms`,
    `  check           : ${lulus} lulus, ${gagal} GAGAL`,
    // Iterasi yang dibuang berarti k6 TIDAK SANGGUP mengirim pada laju yang
    // diminta. Kalau angkanya besar, laju yang tertulis di grafik bukan laju
    // yang sesungguhnya terjadi — dan itu harus terlihat, bukan tersembunyi.
    `  iterasi dibuang : ${j ? j.values.count : 0}`,
    '='.repeat(54),
    '',
  ];

  const keluaran = { stdout: baris.join('\n') };

  // /hasil di-mount dari beban/hasil. Kalau tidak ada, k6 tetap jalan dan
  // ringkasannya tetap tercetak — hanya berkasnya yang tidak tersimpan.
  const berkas = __ENV.HASIL_JSON;
  if (berkas) keluaran[berkas] = JSON.stringify(data, null, 2);

  return keluaran;
}
