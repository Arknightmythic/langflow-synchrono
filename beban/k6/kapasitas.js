// KAPASITAS — berapa permintaan per detik yang sanggup dilayani, titik.
//
// Berbeda dari skenario lain, dan perbedaannya penting.
//
// `status`, `dasar`, dan `aturan` memakai ARRIVAL RATE: k6 mengirim pada laju
// yang ditentukan, entah sasarannya sanggup atau tidak. Itu yang benar untuk
// membandingkan latency pada beban yang sama. Tapi begitu sasarannya kewalahan,
// angka rps yang dilaporkan jadi sulit dibaca — ia bercampur dengan antrean
// yang menumpuk, VU yang menunggu, dan ekor graceful stop setelah beban
// berhenti.
//
// Di sini pakai CONSTANT VUS, alias closed loop: sejumlah penelepon tetap, tiap
// satu mengirim permintaan berikutnya HANYA setelah yang sebelumnya dijawab.
// Antrean tidak pernah menumpuk, jadi `iterations/s` yang keluar adalah
// kapasitas sesungguhnya pada tingkat konkurensi itu — bukan campuran kapasitas
// dan penumpukan.
//
// Jalankan pada beberapa nilai VU untuk melihat kurvanya. Kapasitas yang
// BERHENTI NAIK saat VU ditambah berarti sudah mentok:
//
//     .\kapasitas.ps1 -Target langflow
//
// atau satu per satu:
//
//     k6 run -e TARGET=langflow -e VU=4 -e DETIK=20 beban/k6/kapasitas.js

import {
  bacaStatus, berkasAcak, TAG_UMUM, ringkasan, RINGKAS_TREND,
} from './umum.js';

const VU = Number(__ENV.VU || 4);
const DETIK = Number(__ENV.DETIK || 20);

export const options = {
  scenarios: {
    kapasitas: {
      executor: 'constant-vus',
      vus: VU,
      duration: `${DETIK}s`,
      // Tidak ada gracefulStop yang perlu diperhitungkan: closed loop tidak
      // meninggalkan antrean, jadi penyebut rps-nya bersih.
      gracefulStop: '10s',
      tags: { skenario: 'kapasitas', vu: String(VU) },
    },
  },
  tags: { ...TAG_UMUM, vu: String(VU) },
  summaryTrendStats: RINGKAS_TREND,
  // Tidak ada ambang. Skenario ini memang dijalankan sampai mentok; kegagalan
  // ambang di sini bukan informasi, cuma derau.
  thresholds: {},
};

export default function () {
  bacaStatus(berkasAcak());
}

export function handleSummary(data) {
  return ringkasan(data);
}
