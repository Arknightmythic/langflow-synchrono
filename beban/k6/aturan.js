// BACA ATURAN GRADE — balasan paling besar di antara semua endpoint.
//
// Isinya enam grade lengkap dengan kriteria, pita skor, dan ambang matching:
// beberapa kilobyte JSON dari tiga tabel. Skenario ini menyorot sisi yang tidak
// terlihat di endpoint lain — biaya menyusun dan mengirimkan balasan besar,
// bukan biaya menunggu basis data.
//
// Di sinilah selubung Langflow paling terasa: seluruh JSON itu harus
// di-escape jadi satu STRING, dijejalkan ke dalam struktur balasan berlapis,
// lalu diurai lagi di sisi klien.
//
//     k6 run -e TARGET=service beban/k6/aturan.js

import { bacaAturan, lajuTetap, TAG_UMUM, ringkasan, RINGKAS_TREND } from './umum.js';

const RPS = Number(__ENV.RPS || 20);
const DETIK = Number(__ENV.DETIK || 60);

export const options = {
  scenarios: lajuTetap(RPS, DETIK, 'aturan'),
  tags: TAG_UMUM,
  summaryTrendStats: RINGKAS_TREND,
  thresholds: {
    'http_req_duration{name:config-rules}': ['p(95)<5000'],
    checks: ['rate>0.99'],
  },
};

export default function () {
  bacaAturan();
}

// Ringkasan dipakai bersama oleh semua skenario — lihat umum.js.
export function handleSummary(data) {
  return ringkasan(data);
}
