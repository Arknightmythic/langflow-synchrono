// POLLING STATUS — skenario yang paling penting dari semuanya.
//
// Inilah satu-satunya endpoint yang dipanggil BERULANG-ULANG. Portal menanyakan
// status tiap 2-3 detik untuk setiap berkas yang sedang digrading, jadi satu
// unggahan bisa berarti puluhan permintaan ke sini sementara dispatch-nya cuma
// satu. Kalau hanya satu skenario yang sempat dijalankan, jalankan yang ini.
//
//     k6 run -e TARGET=service  beban/k6/status.js
//     k6 run -e TARGET=langflow beban/k6/status.js
//
// MODE=batas menaikkan lajunya bertahap sampai salah satu sisi menyerah — itu
// yang menjawab "berapa banyak yang sanggup ditangani", bukan sekadar "berapa
// cepat satu permintaan".

import {
  bacaStatus, berkasAcak, lajuTetap, lajuNaik, TAG_UMUM, ringkasan, RINGKAS_TREND,
} from './umum.js';

const MODE = __ENV.MODE || 'setara';
const RPS = Number(__ENV.RPS || 20);
const DETIK = Number(__ENV.DETIK || 60);

export const options = {
  scenarios: MODE === 'batas'
    ? lajuNaik(Number(__ENV.DARI || 5), Number(__ENV.SAMPAI || 200),
               Number(__ENV.TAHAP || 20), 'batas')
    : lajuTetap(RPS, DETIK, 'setara'),
  tags: TAG_UMUM,
  summaryTrendStats: RINGKAS_TREND,
  thresholds: {
    'http_req_duration{name:grading-status}': ['p(95)<5000'],
    checks: ['rate>0.99'],
  },
};

export default function () {
  bacaStatus(berkasAcak());
}

// Ringkasan dipakai bersama oleh semua skenario — lihat umum.js.
export function handleSummary(data) {
  return ringkasan(data);
}
