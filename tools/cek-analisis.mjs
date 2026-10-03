// Checks that web/js/analysis.js still gives the same results as the service.
// Read-only: GET /rules, plus N PATCH requests with dryRun: true (nothing is written).
//
//   SERVICE_URL=http://192.168.2.107:7860 API_KEY=... node tools/cek-analisis.mjs [--acak 200]
import { analyseGlobal, analyseMatching } from "../web/js/analysis.js";

const base = (process.env.SERVICE_URL || "http://localhost:7860").replace(/\/+$/, "");
const key = process.env.API_KEY || "";
const flag = process.argv.indexOf("--acak");
const randomCases = flag >= 0 ? Number(process.argv[flag + 1] || 100) : 0;

async function call(method, path, body) {
  const response = await fetch(`${base}${path}`, {
    method,
    headers: { "x-api-key": key, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok && response.status !== 409) {
    throw new Error(`${method} ${path}: HTTP ${response.status} ${JSON.stringify(data)}`);
  }
  return data;
}

let checked = 0;
let mismatches = 0;

function compare(label, mine, theirs) {
  checked += 1;
  if (JSON.stringify(mine) === JSON.stringify(theirs)) return;
  mismatches += 1;
  if (mismatches <= 5) {
    console.log(`BEDA ${label}\n  UI     : ${JSON.stringify(mine)}\n  service: ${JSON.stringify(theirs)}`);
  }
}

function pick(m) {
  return { maxScoreByMissingCount: m.maxScoreByMissingCount, warnings: m.warnings };
}

// Small seeded generator so a failing run can be repeated.
function generator(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rules = await call("GET", "/api/v1/config/rules");
for (const grade of rules.grades) {
  if (grade.matching?.analysis) {
    compare(`grade ${grade.gradeLetter} (berlaku)`, pick(analyseMatching(grade.matching)), pick(grade.matching.analysis));
  }
}
compare("global (berlaku)", analyseGlobal({ ...rules.global.grading, ...rules.global.matching }), rules.global.analysis.warnings);

const rand = generator(20261003);
const choice = (list) => list[Math.floor(rand() * list.length)];
const sample = (list, count) => [...list].sort(() => rand() - 0.5).slice(0, count);
for (let i = 0; i < randomCases; i += 1) {
  const grade = choice(rules.grades.filter((g) => g.gradeId <= 5 && g.matching?.weights));
  const available = grade.matching.availableElements || { weights: rules.matchingElements, missingElements: rules.matchingElements };
  const elements = sample(available.weights, 1 + Math.floor(rand() * Math.min(5, available.weights.length)));
  const cuts = [...new Set(Array.from({ length: elements.length - 1 }, () => 1 + Math.floor(rand() * 99)))].sort((a, b) => a - b);
  while (cuts.length < elements.length - 1) cuts.push(cuts.length ? cuts[cuts.length - 1] + 1 : 1);
  const bounds = [0, ...cuts, 100];
  const weights = Object.fromEntries(elements.map((e, j) => [e, bounds[j + 1] - bounds[j]]));
  const autoScoreMin = choice([80.001, 85, 87.5, 90, 60, 99.99]);
  const reviewScoreMax = choice([autoScoreMin, 85, 90, 81]);
  const matching = {
    weights,
    missingElements: sample(available.missingElements, Math.floor(rand() * 5)),
    autoMissingMax: choice([null, 0, 1, 2, 99]),
    autoScoreMin,
    reviewMissingCount: choice([null, 0, 1, 2, 3, 99]),
    reviewScoreMin: Math.min(reviewScoreMax, choice([80, 70, 85, 0, 89.99999999999999])),
    reviewScoreMax,
  };
  const result = await call("PATCH", `/api/v1/config/rules/${grade.gradeId}`, { matching, dryRun: true });
  if (result.after?.matching?.analysis) {
    compare(`acak #${i + 1} grade ${grade.gradeLetter} ${JSON.stringify(matching)}`,
      pick(analyseMatching(result.after.matching)), pick(result.after.matching.analysis));
  } else if (result.problems?.length) {
    console.log(`lewati acak #${i + 1}: ${result.problems[0]}`);
  }
}

console.log(`${checked} pembandingan, ${mismatches} beda — ${base}`);
process.exit(mismatches ? 1 : 0);
