// GARIS DASAR: hanya /health, yang tidak menyentuh PostgreSQL maupun S3.
//
// Inilah harga lapisan HTTP-nya saja. Angka dari skenario lain selalu memuat
// biaya basis data, dan tanpa garis dasar ini tidak ada cara memisahkan
// keduanya — sebuah selisih 40 ms tidak berarti apa-apa sampai diketahui
// apakah ia datang dari framework atau dari satu perjalanan ke PostgreSQL.
//
//     k6 run -e TARGET=service beban/k6/dasar.js

import { periksaKesehatan, lajuTetap, TAG_UMUM, ringkasan, RINGKAS_TREND } from './umum.js';

const RPS = Number(__ENV.RPS || 50);
const DETIK = Number(__ENV.DETIK || 60);

export const options = {
  scenarios: lajuTetap(RPS, DETIK, 'dasar'),
  tags: TAG_UMUM,
  summaryTrendStats: RINGKAS_TREND,
  thresholds: {
    // Dilaporkan, bukan untuk menggagalkan. Ambang yang menggagalkan akan
    // menghentikan jalan Langflow di tengah dan justru menghilangkan data yang
    // sedang dicari.
    'http_req_duration{name:health}': ['p(95)<2000'],
    checks: ['rate>0.99'],
  },
};

export default function () {
  periksaKesehatan();
}

// Ringkasan dipakai bersama oleh semua skenario — lihat umum.js.
export function handleSummary(data) {
  return ringkasan(data);
}
