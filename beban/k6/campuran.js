// CAMPURAN — bentuk beban yang menyerupai pemakaian sesungguhnya.
//
// Tiga skenario berjalan BERSAMAAN, dengan laju yang sebanding dengan
// kenyataannya:
//
//   polling status   laju penuh    dipanggil tiap 2-3 detik per berkas
//   baca aturan      1/10 laju     dibuka saat orang membuka menu Rule
//   kirim job        1/60 laju     satu per unggahan berkas
//
// Kenapa perbandingannya dibuat setimpang seperti itu: menjalankan dispatch
// pada laju yang sama dengan polling bukan simulasi, melainkan pengujian
// grading. Tiap dispatch melepas pekerjaan yang makan CPU penuh selama
// beberapa detik, dan dalam hitungan detik yang terukur bukan lagi lapisan API
// melainkan DuckDB. Yang ingin diketahui di sini justru sebaliknya: seberapa
// baik API tetap melayani SELAGI grading berjalan di latar.
//
//     k6 run -e TARGET=service beban/k6/campuran.js
//
// CATATAN. Skenario ini MENULIS: tiap dispatch menambah baris di `grading_jobs`
// dan menimpa enriched.parquet berkas yang bersangkutan. Itu aman diulang —
// tidak ada yang rusak — tapi jangan jalankan sambil ada yang memakai data
// hasil grading untuk hal lain.

import {
  bacaStatus, bacaAturan, kirimJob, berkasAcak, TAG_UMUM, ringkasan, RINGKAS_TREND,
} from './umum.js';

const RPS = Number(__ENV.RPS || 20);
const DETIK = Number(__ENV.DETIK || 120);

function skenario(nama, rate, fungsi) {
  return {
    [nama]: {
      executor: 'constant-arrival-rate',
      rate: Math.max(1, Math.round(rate)),
      timeUnit: rate < 1 ? '60s' : '1s',
      duration: `${DETIK}s`,
      preAllocatedVUs: 10,
      maxVUs: 300,
      exec: fungsi,
      tags: { skenario: nama },
    },
  };
}

export const options = {
  scenarios: {
    ...skenario('polling', RPS, 'polling'),
    ...skenario('aturan', Math.max(1, RPS / 10), 'aturan'),
    // Dinyatakan per 60 detik, bukan per detik: satu unggahan per menit jauh
    // lebih mendekati kenyataan daripada satu per detik.
    ...skenario('unggah', 0.5, 'unggah'),
  },
  tags: TAG_UMUM,
  summaryTrendStats: RINGKAS_TREND,
  thresholds: {
    'http_req_duration{name:grading-status}': ['p(95)<5000'],
    'http_req_duration{name:config-rules}': ['p(95)<5000'],
    checks: ['rate>0.98'],
  },
};

export function polling() {
  bacaStatus(berkasAcak());
}

export function aturan() {
  bacaAturan();
}

export function unggah() {
  // Berkas kecil saja. `uji-besar` (299 ribu baris) akan membuat grading
  // memakan CPU selama belasan detik dan mengubah pengujian ini jadi pengujian
  // DuckDB.
  kirimJob(berkasAcak());
}

// Ringkasan dipakai bersama oleh semua skenario — lihat umum.js.
export function handleSummary(data) {
  return ringkasan(data);
}
