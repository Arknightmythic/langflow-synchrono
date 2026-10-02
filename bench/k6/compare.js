// Grading + matching benchmark, identical for both services.
//   k6 run -e TARGET=old -e BASE=http://srb-old:8000 compare.js
//   k6 run -e TARGET=new -e BASE=http://srb-new-api:8000 compare.js
// Phase 1 (sequential): every file is graded then matched, ROUNDS times.
// Phase 2 (concurrent): all files are graded at once, then all matched at once.
import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Trend, Counter } from 'k6/metrics';

const TARGET = __ENV.TARGET || 'old';
const BASE = __ENV.BASE || 'http://srb-old:8000';
const HARNESS = __ENV.HARNESS || 'http://srb-cb:8000';
const FILES = (__ENV.FILES || 'A,B,C,D,E').split(',').map((s) => s.trim()).filter(Boolean);
const ROUNDS = Number(__ENV.ROUNDS || 3);
const CONCURRENT = (__ENV.CONCURRENT || '1') === '1';
const MASTER_ID = __ENV.MASTER_ID || 'um-master';
const MASTER_KEY = __ENV.MASTER_KEY || 'master/um-master.parquet';
const BUCKET = __ENV.BUCKET || 'bench';
const POLL = Number(__ENV.POLL || 1);
const NOISE_RPS = Number(__ENV.NOISE_RPS || 2);
const LIMIT_MIN = Number(__ENV.LIMIT_MIN || 240);
const RUN = __ENV.RUN_ID || `${Date.now()}`;
const HEADERS = { headers: { 'Content-Type': 'application/json', 'x-api-key': __ENV.API_KEY || 'bench-key' } };

const gradingE2e = new Trend('grading_e2e_ms', true);
const matchingE2e = new Trend('matching_e2e_ms', true);
const dispatchMs = new Trend('dispatch_ms', true);
const failures = new Counter('pipeline_failures');

const thresholds = { 'http_req_duration{scenario:noise}': ['p(95)>=0'] };
for (const f of FILES) {
  thresholds[`grading_e2e_ms{file:${f},mode:seq}`] = ['max>=0'];
  thresholds[`matching_e2e_ms{file:${f},mode:seq}`] = ['max>=0'];
}

export const options = {
  scenarios: {
    pipeline: { executor: 'shared-iterations', vus: 1, iterations: 1, maxDuration: `${LIMIT_MIN}m`,
                exec: 'pipeline', tags: { scenario: 'pipeline' } },
    ...(NOISE_RPS > 0 ? {
      noise: { executor: 'constant-arrival-rate', rate: NOISE_RPS, timeUnit: '1s',
               duration: `${LIMIT_MIN}m`, preAllocatedVUs: 5, maxVUs: 50, exec: 'noise',
               gracefulStop: '5s', tags: { scenario: 'noise' } },
    } : {}),
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  thresholds,
};

function emit(record) {
  console.log(`RESULT ${JSON.stringify({ target: TARGET, run: RUN, ...record })}`);
}

function parseTime(text) {
  if (!text) return null;
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : `${text}Z`;
  const t = Date.parse(iso.replace(' ', 'T'));
  return Number.isNaN(t) ? null : t;
}

function dispatchGrading(file, tag) {
  const fileId = `${TARGET}-${file}-${tag}-${RUN}`;
  const key = `um-${file}/raw/data.csv`;
  const started = Date.now();
  const res = http.post(`${BASE}/api/v1/grading/jobs`, JSON.stringify({
    fileId, s3Bucket: BUCKET, rawSourceKey: key, parquetKey: key,
    enrichedParquetKey: `${fileId}/enriched.parquet`,
  }), { ...HEADERS, tags: { step: 'grading_dispatch' } });
  dispatchMs.add(res.timings.duration, { step: 'grading' });
  const ok = check(res, { 'grading accepted': (r) => r.status === 202 });
  return { file, fileId, started, ok, body: res.body };
}

function waitGrading(job, mode, round) {
  let body = null;
  while (Date.now() - job.started < LIMIT_MIN * 60000) {
    const s = http.get(`${BASE}/api/v1/grading/jobs/${job.fileId}`, { ...HEADERS, tags: { step: 'grading_status' } });
    if (s.status === 200) {
      body = s.json();
      if (body.done) break;
    }
    sleep(POLL);
  }
  const finished = parseTime(body && body.finishedAt);
  const e2e = finished ? Math.max(finished - job.started, 0) : Date.now() - job.started;
  gradingE2e.add(e2e, { file: job.file, mode });
  const summary = (body && body.result && body.result.summary) || {};
  emit({ mode, round, file: job.file, step: 'grading', fileId: job.fileId, status: body && body.status,
         e2eMs: e2e, engineMs: body && body.gradingDurationMs, klLoadMs: (body && body.klLoadMs) || null,
         rows: body && body.recordCount, grade: summary.gradeLetter, score: summary.qualityScore,
         error: body && body.error });
  return body && body.status === 'COMPLETED';
}

function dispatchMatching(file, fileId, tag) {
  const jobId = `${TARGET}-match-${file}-${tag}-${RUN}`;
  if (TARGET === 'old') {
    http.post(`${HARNESS}/prepare`, JSON.stringify({ jobId, fileId, masterFileId: MASTER_ID }), HEADERS);
  }
  const payload = {
    jobId, fileId, masterFileId: MASTER_ID, actor: 'k6', callbackUrl: `${HARNESS}/cb/${TARGET}`,
    s3Bucket: BUCKET, incomingFile: { s3Key: `${fileId}/enriched.parquet` },
    masterDataFile: { s3Key: MASTER_KEY },
  };
  const started = Date.now();
  const res = http.post(`${BASE}/api/v1/run/matching-dispatch`, JSON.stringify({
    tweaks: { 'MatchingDispatch-b4819': { payload: JSON.stringify(payload) } },
  }), { ...HEADERS, tags: { step: 'matching_dispatch' } });
  dispatchMs.add(res.timings.duration, { step: 'matching' });
  const ok = check(res, { 'matching accepted': (r) => r.status === 200 });
  return { file, jobId, started, ok };
}

function waitMatching(job, mode, round) {
  let cb = null;
  let received = null;
  while (Date.now() - job.started < LIMIT_MIN * 60000) {
    const s = http.get(`${HARNESS}/cb/${job.jobId}`, { tags: { step: 'matching_callback' } });
    if (s.status === 200) {
      const item = s.json();
      cb = item.body;
      received = Math.round(item.receivedAt * 1000);
      break;
    }
    sleep(POLL);
  }
  const e2e = received ? Math.max(received - job.started, 0) : Date.now() - job.started;
  const metrics = (cb && cb.metrics) || {};
  matchingE2e.add(e2e, { file: job.file, mode });
  emit({ mode, round, file: job.file, step: 'matching', jobId: job.jobId,
         status: cb ? cb.status : 'TIMEOUT', e2eMs: e2e, stages: metrics.stageDurations || {},
         auto: metrics.autoCount, review: metrics.reviewCount, unmatch: metrics.unmatchCount,
         conflict: metrics.conflictCount, error: cb ? cb.error : 'no callback' });
  return cb && cb.status === 'COMPLETED';
}

export function pipeline() {
  for (let round = 1; round <= ROUNDS; round++) {
    for (const file of FILES) {
      const g = dispatchGrading(file, `s${round}`);
      if (!g.ok) { failures.add(1); emit({ mode: 'seq', round, file, step: 'grading', error: g.body }); continue; }
      if (!waitGrading(g, 'seq', round)) { failures.add(1); continue; }
      const m = dispatchMatching(file, g.fileId, `s${round}`);
      if (!m.ok) { failures.add(1); continue; }
      if (!waitMatching(m, 'seq', round)) failures.add(1);
    }
  }
  if (CONCURRENT) {
    const started = Date.now();
    const gradings = FILES.map((f) => dispatchGrading(f, 'c'));
    const graded = gradings.filter((g) => g.ok && waitGrading(g, 'concurrent', 0));
    const gradingWall = Date.now() - started;
    const matchStart = Date.now();
    const matchings = graded.map((g) => dispatchMatching(g.file, g.fileId, 'c'));
    const matched = matchings.filter((m) => m.ok && waitMatching(m, 'concurrent', 0));
    emit({ mode: 'concurrent', step: 'makespan', files: FILES.length, graded: graded.length,
           matched: matched.length, gradingWallMs: gradingWall, matchingWallMs: Date.now() - matchStart,
           totalWallMs: Date.now() - started });
  }
  exec.test.abort('pipeline finished');
}

export function noise() {
  http.get(`${BASE}/api/v1/grading/jobs/${TARGET}-${FILES[0]}-s1-${RUN}`, { ...HEADERS, tags: { step: 'noise_status' } });
}

export function handleSummary(data) {
  return { [`/results/k6-${TARGET}-${RUN}.json`]: JSON.stringify(data, null, 2) };
}
