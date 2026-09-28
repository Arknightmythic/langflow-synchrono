// PER API — tiap endpoint grading diukur SENDIRI-SENDIRI.
//
// Skenario lain mencampur beberapa endpoint (campuran) atau hanya memukul satu
// (status, aturan). Yang tidak terjawab keduanya: berapa ongkos MASING-MASING
// endpoint, pada beban yang sama, di kedua platform.
//
// Itu yang diukur di sini. Lima endpoint, satu per satu, BERURUTAN — bukan
// bersamaan. Kalau dijalankan bersamaan, endpoint yang berat akan menghabiskan
// CPU dan membuat yang ringan terlihat lambat; yang terbaca bukan lagi ongkos
// endpoint itu sendiri.
//
//     k6 run -e TARGET=service  per_api.js
//     k6 run -e TARGET=langflow per_api.js
//
// URUTAN DAN JATAH WAKTUNYA
//
//   0 dtk   config-rules          baca aturan, tidak menulis
//   D       config-rules-update   PATCH satu grade, dryRun — TIDAK menulis
//   2D      grading-status        baca status pekerjaan
//   3D      grading-dispatch      terima pekerjaan lalu lepas ke latar
//   4D      grading               pipeline penuh, SINKRON, berkas kecil
//
// `grading` diberi laju paling rendah karena ia benar-benar menggrading berkas
// sampai selesai di dalam permintaan itu juga. Menyamakan lajunya dengan yang
// lain berarti mengukur antrean, bukan endpointnya.
//
// KENAPA dryRun WAJIB
//
// `config-rules-update` menulis ke tabel aturan. Menjalankannya ratusan kali
// tanpa dryRun akan MENGUBAH konfigurasi grading, dan berkas yang digrading
// sesudahnya mendapat nilai yang berbeda. Kedua sisi karena itu dipaksa
// dryRun; yang terukur tetap seluruh jalur validasinya, hanya penulisan
// terakhirnya yang dilewati.

import http from 'k6/http';
import { check } from 'k6';
import { Trend, Counter } from 'k6/metrics';

import {
  BASE, TARGET, BUCKET, berkasAcak, bacaAturan, bacaStatus, kirimJob,
  TAG_UMUM, RINGKAS_TREND,
} from './umum.js';

// LAJUNYA SENGAJA RENDAH, dan tidak mengambil dari -Rps.
//
// Yang dicari ongkos tiap endpoint, bukan perilakunya saat kelebihan beban —
// itu sudah diukur skenario `status` dan `kapasitas`. Begitu lajunya melewati
// kapasitas salah satu sisi, yang terbaca panjang antrean, dan endpoint yang
// ringan pun ikut terlihat lambat. 2 permintaan/detik ada di bawah kapasitas
// kedua sisi, jadi keduanya melayani seluruhnya.
const RPS = Number(__ENV.RPS_API || 2);
// k6 menuntut `rate` bilangan bulat, jadi laju di bawah 1/detik ditulis
// sebagai 1 per N detik.
const BERAT_TIAP = Number(__ENV.BERAT_TIAP || 2);  // 1 permintaan tiap N detik
const D = Number(__ENV.DETIK_API || 25);     // jatah tiap endpoint
const JEDA = D + 20;                         // jarak mulai antar endpoint

// Node id flow Langflow. Tetap, diturunkan dari nama endpoint dan nama
// komponen — lihat catatan yang sama di umum.js.
const NODE = {
  update: 'GradingRuleUpdate-ea0f7',
  grading: 'OpenGradingSession-ab8d5',
};

const KUNCI = TARGET === 'service'
  ? (__ENV.SERVICE_API_KEY || 'synchrono-bench-key')
  : (__ENV.LANGFLOW_API_KEY || '');

function header() {
  return { headers: { 'Content-Type': 'application/json', 'x-api-key': KUNCI } };
}

function lewatFlow(endpoint, node, param, nama) {
  return http.post(
    `${BASE}/api/v1/run/${endpoint}?stream=false`,
    JSON.stringify({
      output_type: 'chat', input_type: 'text', input_value: '',
      tweaks: { [node]: param },
    }),
    { ...header(), tags: { name: nama } },
  );
}

// Satu Trend per endpoint. http_req_duration global tidak cukup: ia mencampur
// kelimanya jadi satu angka, dan justru pemisahan itulah gunanya berkas ini.
const T = {
  'config-rules': new Trend('api_config_rules', true),
  'config-rules-update': new Trend('api_config_rules_update', true),
  'grading-status': new Trend('api_grading_status', true),
  'grading-dispatch': new Trend('api_grading_dispatch', true),
  'grading': new Trend('api_grading_sinkron', true),
};
const GAGAL = new Counter('api_gagal');

function catat(nama, res, lulus) {
  T[nama].add(res.timings.duration);
  if (!lulus) GAGAL.add(1, { endpoint: nama });
}

export const options = {
  scenarios: {
    rules: {
      executor: 'constant-arrival-rate', rate: RPS, timeUnit: '1s',
      duration: `${D}s`, preAllocatedVUs: 5, maxVUs: 30, gracefulStop: '15s',
      exec: 'rules', startTime: '0s', tags: { endpoint: 'config-rules' },
    },
    rulesUpdate: {
      executor: 'constant-arrival-rate', rate: RPS, timeUnit: '1s',
      duration: `${D}s`, preAllocatedVUs: 5, maxVUs: 30, gracefulStop: '15s',
      exec: 'rulesUpdate', startTime: `${1 * JEDA}s`,
      tags: { endpoint: 'config-rules-update' },
    },
    status: {
      executor: 'constant-arrival-rate', rate: RPS, timeUnit: '1s',
      duration: `${D}s`, preAllocatedVUs: 5, maxVUs: 30, gracefulStop: '15s',
      exec: 'status', startTime: `${2 * JEDA}s`,
      tags: { endpoint: 'grading-status' },
    },
    dispatch: {
      executor: 'constant-arrival-rate', rate: RPS, timeUnit: '1s',
      duration: `${D}s`, preAllocatedVUs: 5, maxVUs: 30, gracefulStop: '15s',
      exec: 'dispatch', startTime: `${3 * JEDA}s`,
      tags: { endpoint: 'grading-dispatch' },
    },
    gradingSinkron: {
      executor: 'constant-arrival-rate', rate: 1, timeUnit: `${BERAT_TIAP}s`,
      duration: `${D}s`, preAllocatedVUs: 3, maxVUs: 20, gracefulStop: '30s',
      exec: 'gradingSinkron', startTime: `${4 * JEDA}s`,
      tags: { endpoint: 'grading' },
    },
  },
  tags: TAG_UMUM,
  summaryTrendStats: ['count'].concat(RINGKAS_TREND),
  // Tanpa ambang: tujuannya membandingkan, bukan lulus atau gagal.
  thresholds: {},
};

// ── 1. config-rules ────────────────────────────────────────────────────────
export function rules() {
  const res = bacaAturan();
  catat('config-rules', res, res.status === 200);
}

// ── 2. config-rules-update ─────────────────────────────────────────────────
//
// Grade 6 (F) yang disentuh, dengan ambang skor DISETEL KE NILAI YANG SAMA
// dengan yang sudah tersimpan (min 0, maks 9). Jadi meskipun dryRun gagal,
// tidak ada yang berubah — dua lapis pengaman terhadap konfigurasi yang
// dipakai bersama.
//
// `updatedBy` saja tidak cukup: sisi service menolak perubahan yang tidak
// memuat salah satu dari criteria, score, atau matching.
const UBAH = { score: { min: 0, max: 9 }, updatedBy: 'beban-per-api', dryRun: true };

export function rulesUpdate() {
  const muatan = Object.assign({ gradeId: 6 }, UBAH);
  let res;
  if (TARGET === 'service') {
    res = http.patch(`${BASE}/api/v1/config/rules/6`, JSON.stringify(UBAH),
      { ...header(), tags: { name: 'config-rules-update' } });
  } else {
    res = lewatFlow('config-rules-update', NODE.update,
      { payload: JSON.stringify(muatan), dry_run: true }, 'config-rules-update');
  }
  const lulus = check(res, {
    'update dijawab': (r) => r.status === 200 || r.status === 409,
  });
  catat('config-rules-update', res, lulus);
}

// ── 3. grading-status ──────────────────────────────────────────────────────
export function status() {
  const res = bacaStatus(berkasAcak());
  catat('grading-status', res, res.status === 200);
}

// ── 4. grading-dispatch ────────────────────────────────────────────────────
export function dispatch() {
  const res = kirimJob(berkasAcak());
  catat('grading-dispatch', res, res.status === 200 || res.status === 202);
}

// ── 5. grading (sinkron) ───────────────────────────────────────────────────
//
// Satu-satunya endpoint yang benar-benar menggrading di dalam permintaannya.
// Berkas kecil dipakai dengan sengaja: yang dicari ongkos endpointnya, bukan
// lama menggrading berkas besar — itu sudah diukur skenario `skala_jutaan`.
export function gradingSinkron() {
  const berkas = berkasAcak();
  let res;
  if (TARGET === 'service') {
    res = http.post(`${BASE}/api/v1/grading/run`,
      JSON.stringify({
        fileId: berkas,
        s3Bucket: BUCKET,
        parquetKey: `uploads/${berkas}/data.parquet`,
        enrichedParquetKey: `uji/per-api-${berkas}.parquet`,
      }),
      { ...header(), timeout: '120s', tags: { name: 'grading' } });
  } else {
    res = lewatFlow('grading', NODE.grading, {
      file_id: berkas,
      s3_bucket: BUCKET,
      parquet_key: `uploads/${berkas}/data.parquet`,
      s3_endpoint: '',
      enriched_key: `uji/per-api-${berkas}.parquet`,
    }, 'grading');
  }
  const lulus = check(res, { 'grading 200': (r) => r.status === 200 });
  catat('grading', res, lulus);
}

// Ringkasan sendiri: yang dicari perbandingan ANTAR endpoint, dan itu tidak
// terbaca dari satu angka http_req_duration yang mencampur kelimanya.
export function handleSummary(data) {
  const urut = [
    ['config-rules', 'api_config_rules'],
    ['config-rules-update', 'api_config_rules_update'],
    ['grading-status', 'api_grading_status'],
    ['grading-dispatch', 'api_grading_dispatch'],
    ['grading (sinkron)', 'api_grading_sinkron'],
  ];

  const baris = [
    '',
    `======  PER API — ${TARGET.toUpperCase()}  ${'='.repeat(30 - TARGET.length)}`,
    '  endpoint                 n      avg       p95       p99      maks',
    '  ' + '-'.repeat(64),
  ];

  for (const [nama, kunci] of urut) {
    const m = data.metrics[kunci];
    if (!m) { baris.push(`  ${nama.padEnd(22)}  (tidak terukur)`); continue; }
    const v = m.values;
    baris.push(
      '  ' + nama.padEnd(22) +
      String(v.count).padStart(5) +
      (v.avg || 0).toFixed(1).padStart(9) +
      (v['p(95)'] || 0).toFixed(1).padStart(10) +
      (v['p(99)'] || 0).toFixed(1).padStart(10) +
      (v.max || 0).toFixed(1).padStart(10)
    );
  }

  const g = data.metrics.api_gagal;
  const c = data.metrics.checks;
  baris.push('  ' + '-'.repeat(64));
  baris.push(`  satuan ms  |  gagal: ${g ? g.values.count : 0}  |  check: ` +
             `${c ? c.values.passes : 0} lulus, ${c ? c.values.fails : 0} GAGAL`);

  // Check yang gagal disebut NAMANYA. Tanpa ini, satu angka "33 GAGAL" tidak
  // memberi tahu endpoint mana yang bermasalah, dan menebaknya memakan waktu.
  const gugus = [];
  (function telusuri(g) {
    for (const c of (g.checks || [])) if (c.fails > 0) gugus.push(c);
    for (const a of (g.groups || [])) telusuri(a);
  })(data.root_group || {});
  if (gugus.length) {
    baris.push('  check yang gagal:');
    for (const c of gugus) {
      baris.push(`    ${c.name.padEnd(34)} ${c.fails} gagal / ${c.passes + c.fails}`);
    }
  }

  baris.push('='.repeat(54));
  baris.push('');

  const keluaran = { stdout: baris.join('\n') };
  const berkas = __ENV.HASIL_JSON;
  if (berkas) keluaran[berkas] = JSON.stringify(data, null, 2);
  return keluaran;
}
