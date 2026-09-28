// SKALA JUTAAN — apakah platform masih berpengaruh kalau berkasnya jutaan baris?
//
// Pertanyaan yang jujur, karena jawabannya tidak otomatis "ya". Grading
// dikerjakan `lib/` yang SAMA di thread latar pada kedua platform, jadi
// sebagian besar kerjanya identik. Yang berbeda hanya lapisan API-nya —
// dan pada berkas 3.000 baris lapisan itu memang menentukan.
//
// Tapi ada satu hal yang hanya muncul di skala besar: SELAMA grading berjalan
// (menit, bukan detik), portal terus melakukan polling. Polling itu memakan
// CPU pada mesin yang SAMA dengan yang sedang menggrading. Diukur sebelumnya,
// Langflow memakai 2,4 inti hanya untuk melayani 2 permintaan status per detik,
// dari 4 inti yang tersedia. Jadi dugaannya: di Langflow, polling MERAMPAS CPU
// dari grading, dan berkas yang sama jadi lebih lama selesai.
//
// Itulah yang diukur di sini, dan itu tidak terlihat dari benchmark mana pun
// yang sudah ada.
//
//     k6 run -e TARGET=service  -e BERKAS_BESAR=besar-5000k -e BUCKET=bucket-test skala_jutaan.js
//     k6 run -e TARGET=langflow -e BERKAS_BESAR=besar-5000k -e BUCKET=bucket-test skala_jutaan.js
//
// Dua skenario berjalan BERSAMAAN:
//
//   besar   satu berkas jutaan baris dikirim lalu dipolling sampai selesai —
//           persis yang portal lakukan untuk satu unggahan
//   bising  polling berkas LAIN pada laju tetap — mewakili unggahan lain yang
//           juga sedang ditunggu. Inilah sumber rebutan CPU-nya.
//
// Setel `-e BISING=0` untuk menjalankan tanpa rebutan, sebagai pembanding.

import { sleep } from 'k6';
import exec from 'k6/execution';
import { Trend, Counter } from 'k6/metrics';

import {
  kirimJob, statusTerurai, bacaStatus, berkasAcak, TAG_UMUM, ringkasan,
  RINGKAS_TREND, BUCKET,
} from './umum.js';

const BESAR = __ENV.BERKAS_BESAR || 'besar-5000k';
const BISING = Number(__ENV.BISING ?? 2);       // permintaan status per detik
const JEDA = Number(__ENV.JEDA || 2);           // jeda polling, detik
const BATAS_MENIT = Number(__ENV.BATAS_MENIT || 15);

// Metrik sendiri, karena yang penting di sini tidak terwakili http_req_duration.
const dispatch_ms = new Trend('besar_dispatch_ms', true);
const tuntas_ms = new Trend('besar_tuntas_ms', true);      // dispatch -> done
const engine_ms = new Trend('besar_grading_engine_ms', true);  // kata engine
const polling_ms = new Trend('besar_polling_ms', true);
const jumlah_poll = new Counter('besar_jumlah_poll');

export const options = {
  scenarios: {
    besar: {
      executor: 'shared-iterations',
      vus: 1,
      iterations: 1,
      maxDuration: `${BATAS_MENIT}m`,
      exec: 'besar',
      tags: { skenario: 'besar' },
    },
    ...(BISING > 0 ? {
      bising: {
        executor: 'constant-arrival-rate',
        rate: BISING,
        timeUnit: '1s',
        duration: `${BATAS_MENIT}m`,
        preAllocatedVUs: 10,
        maxVUs: 300,
        exec: 'bising',
        gracefulStop: '3s',
        tags: { skenario: 'bising' },
      },
    } : {}),
  },
  tags: TAG_UMUM,
  summaryTrendStats: RINGKAS_TREND,
  // Tanpa ambang. Jalan ini sengaja didorong sampai salah satu sisi kepayahan;
  // ambang yang gagal di sini bukan informasi.
  thresholds: {},
};

export function besar() {
  console.log(`[besar] mengirim ${BESAR} (bucket ${BUCKET}), bising ${BISING} rps`);

  const mulai = Date.now();
  const res = kirimJob(BESAR);
  dispatch_ms.add(res.timings.duration);
  console.log(`[besar] dispatch ${res.timings.duration.toFixed(0)} ms`);

  let selesai = false;
  let n = 0;
  let lapor = null;

  while (!selesai && Date.now() - mulai < BATAS_MENIT * 60_000) {
    sleep(JEDA);
    const t = Date.now();
    const isi = statusTerurai(BESAR);
    polling_ms.add(Date.now() - t);
    jumlah_poll.add(1);
    n++;
    if (isi && isi.done) {
      selesai = true;
      lapor = isi;
    }
  }

  const tuntas = Date.now() - mulai;
  tuntas_ms.add(tuntas);

  if (lapor) {
    engine_ms.add(lapor.gradingDurationMs || 0);
    console.log(
      `[besar] SELESAI status=${lapor.status} ` +
      `baris=${lapor.recordCount} ` +
      `grade=${(lapor.result && lapor.result.summary || {}).gradeLetter} ` +
      `engine=${lapor.gradingDurationMs} ms ` +
      `tuntas=${tuntas} ms polling=${n}x`
    );
    if (lapor.error) console.log(`[besar] GALAT: ${lapor.error}`);
  } else {
    console.log(`[besar] TIDAK SELESAI dalam ${BATAS_MENIT} menit (${n} polling)`);
  }

  // Menghentikan skenario `bising` juga. Tanpa ini k6 menunggu sampai
  // durasinya habis, dan menitnya terbuang tanpa menambah informasi apa pun.
  exec.test.abort('berkas besar selesai');
}

export function bising() {
  // Berkas LAIN, bukan yang besar — supaya yang terukur rebutan CPU-nya,
  // bukan polling berulang ke baris yang sama.
  bacaStatus(berkasAcak());
}

export function handleSummary(data) {
  return ringkasan(data);
}
