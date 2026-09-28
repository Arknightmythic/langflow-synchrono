// LAPISAN — membedah ke mana perginya waktu pada satu panggilan.
//
// Rangkaian `per_api` menjawab BERAPA lama tiap endpoint. Yang tidak
// dijawabnya: KENAPA. Lewat Langflow, tiga endpoint yang pekerjaannya jauh
// berbeda — membaca beberapa baris aturan, memvalidasi perubahan, membaca satu
// baris status — semuanya mendarat di sekitar 480 ms. Angka yang hampir sama
// untuk pekerjaan yang tidak sama adalah tanda bahwa yang terukur bukan
// pekerjaannya, melainkan ongkos tetap di sekelilingnya.
//
// Berkas ini memisahkan ongkos tetap itu dari pekerjaannya, dengan menembak
// TIGA TINGKAT pada platform yang sama, berurutan, di mesin yang sama:
//
//   lantai   /health (service) atau /health_check (Langflow)
//            Tidak menjalankan flow, tidak menyentuh basis data. Yang terukur
//            murni ongkos masuk-keluar HTTP platform itu sendiri.
//
//   ringan   grading-status — flow 2 node, satu pembacaan kecil PostgreSQL.
//            Flow terkecil yang masih benar-benar bekerja.
//
//   berat    grading sinkron — flow 7 node, pipeline grading penuh atas
//            berkas kecil. Muatan balasannya memuat `gradingDurationMs`, yaitu
//            LAPORAN MESIN GRADING SENDIRI tentang berapa lama ia bekerja.
//
// KENAPA gradingDurationMs ITU KUNCINYA
//
// Kode yang menggrading SAMA PERSIS di kedua platform — node Langflow dan
// handler service memanggil pustaka `lib/` yang sama. Jadi untuk berkas yang
// sama, angka yang dilaporkan mesin seharusnya sama, dan memang begitu.
// Karena itu:
//
//     ongkos di luar mesin = waktu yang diukur klien - yang dilaporkan mesin
//
// Selisih itu adalah SEMUA yang bukan menggrading: HTTP, otentikasi, menyusun
// graf, membuat node, menyalurkan hasil antar node, dan membungkus balasan.
// Diukur di kedua platform, ia langsung menunjukkan di mana perbedaannya, dan
// bukan sekadar bahwa ada perbedaan.
//
// MEMBANDINGKAN 2 NODE DENGAN 7 NODE
//
// `ringan` dan `berat` sengaja dipilih beda jumlah node (2 lawan 7). Kalau
// ongkos tetapnya per PANGGILAN, keduanya menanggung ongkos yang kira-kira
// sama. Kalau ongkosnya per NODE, `berat` menanggung jauh lebih besar. Itu dua
// ramalan yang berbeda, dan pengukuran ini bisa memisahkannya.
//
//     k6 run -e TARGET=service  lapisan.js
//     k6 run -e TARGET=langflow lapisan.js

import http from 'k6/http';
import { check } from 'k6';
import { Trend, Counter } from 'k6/metrics';

import { BASE, TARGET, BUCKET, berkasAcak } from './umum.js';

const KUNCI = TARGET === 'service'
  ? (__ENV.SERVICE_API_KEY || 'synchrono-bench-key')
  : (__ENV.LANGFLOW_API_KEY || '');

// Node id tetap — lihat catatan yang sama di umum.js.
const NODE = {
  status: 'GradingStatus-3cc03',
  grading: 'OpenGradingSession-ab8d5',
};

const RPS = Number(__ENV.RPS_LAPIS || 2);      // untuk lantai & ringan
const D = Number(__ENV.DETIK_LAPIS || 30);     // jatah lantai & ringan
const D_BERAT = Number(__ENV.DETIK_BERAT || 60);
const JEDA = 15;                               // istirahat antar tingkat

// SATU berkas tetap untuk tingkat `berat`, bukan acak seperti tingkat lain.
//
// Tingkat inilah yang memakai `gradingDurationMs` untuk memisahkan pekerjaan
// dari ongkosnya, dan pengurangan itu hanya sah kalau kedua platform
// menggrading isi yang sama. Berkas acak membuat kedua sisi menarik campuran
// ukuran yang berbeda, dan selisih yang muncul sebagiannya jadi selisih
// datanya, bukan selisih platformnya.
const BERKAS_BERAT = __ENV.BERKAS_LAPIS || 'uji-b';

const t_lantai = new Trend('lap_lantai', true);
const t_ringan = new Trend('lap_ringan', true);
const t_berat = new Trend('lap_berat', true);
const t_mesin = new Trend('lap_berat_mesin', true);
const GAGAL = new Counter('lap_gagal');
// Jumlah panggilan dihitung sendiri: `count` pada Trend tidak tersedia di
// semua versi k6, dan ringkasan tanpa n tidak bisa dinilai pembacanya.
const N = {
  lantai: new Counter('lap_n_lantai'),
  ringan: new Counter('lap_n_ringan'),
  berat: new Counter('lap_n_berat'),
};

function header() {
  return { headers: { 'Content-Type': 'application/json', 'x-api-key': KUNCI } };
}

function lewatFlow(endpoint, node, param, nama, batas) {
  return http.post(
    `${BASE}/api/v1/run/${endpoint}?stream=false`,
    JSON.stringify({
      output_type: 'chat', input_type: 'text', input_value: '',
      tweaks: { [node]: param },
    }),
    { ...header(), timeout: batas || '60s', tags: { name: nama } },
  );
}

// Langflow mengubur muatannya di dalam selubung empat lapis, dan isinya masih
// string yang perlu diurai sekali lagi.
function kupas(res) {
  if (res.status < 200 || res.status >= 300) return null;
  try {
    const luar = res.json();
    if (TARGET === 'service') return luar;
    return JSON.parse(luar.outputs[0].outputs[0].results.message.text);
  } catch (e) {
    return null;
  }
}

export const options = {
  scenarios: {
    lantai: {
      executor: 'constant-arrival-rate', rate: RPS, timeUnit: '1s',
      duration: `${D}s`, preAllocatedVUs: 5, maxVUs: 30, gracefulStop: '15s',
      exec: 'lantai', startTime: '0s',
    },
    ringan: {
      executor: 'constant-arrival-rate', rate: RPS, timeUnit: '1s',
      duration: `${D}s`, preAllocatedVUs: 5, maxVUs: 30, gracefulStop: '15s',
      exec: 'ringan', startTime: `${D + JEDA}s`,
    },
    // Lajunya jauh lebih rendah: ia benar-benar menggrading sampai selesai di
    // dalam permintaan itu juga. Disamakan dengan yang lain, yang terukur
    // panjang antrean, bukan ongkos satu panggilan.
    berat: {
      executor: 'constant-arrival-rate', rate: 1, timeUnit: '3s',
      duration: `${D_BERAT}s`, preAllocatedVUs: 3, maxVUs: 10,
      gracefulStop: '90s', exec: 'berat',
      startTime: `${2 * D + 2 * JEDA}s`,
    },
  },
  thresholds: {},
};

export function lantai() {
  // Langflow tidak punya padanan /health yang persis; /health_check yang
  // terdekat, dan sama-sama tidak menyentuh basis data.
  const url = TARGET === 'service' ? `${BASE}/health` : `${BASE}/health_check`;
  const res = http.get(url, { tags: { name: 'lantai' } });
  const lulus = check(res, { 'lantai 2xx': (r) => r.status >= 200 && r.status < 300 });
  if (!lulus) GAGAL.add(1, { tingkat: 'lantai' });
  t_lantai.add(res.timings.duration);
  N.lantai.add(1);
}

export function ringan() {
  const berkas = berkasAcak();
  let res;
  if (TARGET === 'service') {
    res = http.get(`${BASE}/api/v1/grading/jobs/${berkas}?panen=false`,
      { ...header(), tags: { name: 'ringan' } });
  } else {
    res = lewatFlow('grading-status', NODE.status,
      { file_id: berkas, job_id: '' }, 'ringan');
  }
  // Badannya ikut diurai: Langflow membalas 200 juga untuk eksekusi yang
  // gagal, dan latency kegagalan selalu tampak bagus.
  const p = kupas(res);
  const lulus = check(res, { 'ringan berisi': () => p !== null && p.fileId !== undefined });
  if (!lulus) GAGAL.add(1, { tingkat: 'ringan' });
  t_ringan.add(res.timings.duration);
  N.ringan.add(1);
}

export function berat() {
  const berkas = BERKAS_BERAT;
  let res;
  if (TARGET === 'service') {
    res = http.post(`${BASE}/api/v1/grading/run`,
      JSON.stringify({
        fileId: berkas,
        s3Bucket: BUCKET,
        parquetKey: `uploads/${berkas}/data.parquet`,
        enrichedParquetKey: `uji/lapisan-${TARGET}-${berkas}.parquet`,
      }),
      { ...header(), timeout: '120s', tags: { name: 'berat' } });
  } else {
    res = lewatFlow('grading', NODE.grading, {
      file_id: berkas,
      s3_bucket: BUCKET,
      parquet_key: `uploads/${berkas}/data.parquet`,
      s3_endpoint: '',
      enriched_key: `uji/lapisan-${TARGET}-${berkas}.parquet`,
    }, 'berat', '120s');
  }
  const p = kupas(res);
  const lulus = check(res, {
    'berat berisi': () => p !== null && p.gradingDurationMs !== undefined,
  });
  if (!lulus) GAGAL.add(1, { tingkat: 'berat' });
  t_berat.add(res.timings.duration);
  N.berat.add(1);
  // Hanya dicatat kalau panggilannya benar-benar berhasil. Mencampur laporan
  // mesin dari panggilan gagal akan menggeser rata-ratanya diam-diam.
  if (lulus) t_mesin.add(p.gradingDurationMs);
}

export function handleSummary(data) {
  const g = (n, s) => (data.metrics[n] ? data.metrics[n].values[s] : 0);
  const f = (x) => (x || 0).toFixed(1).padStart(9);

  const lantai = g('lap_lantai', 'avg');
  const ringan = g('lap_ringan', 'avg');
  const berat = g('lap_berat', 'avg');
  const mesin = g('lap_berat_mesin', 'avg');

  const n = (k) => String(g(`lap_n_${k}`, 'count')).padStart(5);

  const baris = [
    '',
    `======  LAPISAN — ${TARGET.toUpperCase()}  ${'='.repeat(28 - TARGET.length)}`,
    `  berkas tingkat berat: ${BERKAS_BERAT}`,
    '  tingkat                  n      avg       p95      maks',
    '  ' + '-'.repeat(56),
    '  lantai (HTTP saja)  ' + n('lantai') +
      f(lantai) + f(g('lap_lantai', 'p(95)')) + f(g('lap_lantai', 'max')),
    '  ringan (2 node)     ' + n('ringan') +
      f(ringan) + f(g('lap_ringan', 'p(95)')) + f(g('lap_ringan', 'max')),
    '  berat  (7 node)     ' + n('berat') +
      f(berat) + f(g('lap_berat', 'p(95)')) + f(g('lap_berat', 'max')),
    '  -- lapor mesin --   ' + n('berat') +
      f(mesin) + f(g('lap_berat_mesin', 'p(95)')) + f(g('lap_berat_mesin', 'max')),
    '  ' + '-'.repeat(56),
    '  TURUNAN',
    `    ongkos di luar mesin (berat)   ${(berat - mesin).toFixed(1)} ms`,
    `    tambahan ringan atas lantai    ${(ringan - lantai).toFixed(1)} ms`,
    `  satuan ms  |  gagal: ${g('lap_gagal', 'count')}`,
    '='.repeat(52),
    '',
  ];

  const keluar = { stdout: baris.join('\n') };
  if (__ENV.HASIL_JSON) keluar[__ENV.HASIL_JSON] = JSON.stringify(data, null, 2);
  return keluar;
}
