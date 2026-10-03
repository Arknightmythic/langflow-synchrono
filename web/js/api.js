import { readStore, writeStore } from "./dom.js";

let target = null;

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export function setTarget(next) {
  target = next;
}

export function currentTarget() {
  return target;
}

function keyName() {
  return `synchrono-ui:key:${target?.id ?? "0"}`;
}

export function storedKey() {
  return readStore("session", keyName()) || readStore("local", keyName()) || "";
}

export function keyRemembered() {
  return Boolean(readStore("local", keyName()));
}

export function saveKey(key, remember) {
  writeStore("session", keyName(), remember ? null : key);
  writeStore("local", keyName(), remember ? key : null);
}

function describe(status, body, text) {
  const detail = body && body.detail;
  if (status === 0) return "Service tidak terjangkau dari browser (jaringan terputus atau UI berhenti).";
  if (status === 401 || status === 403) {
    return target?.keyInjected
      ? "API key yang diisi server ditolak service (401). Periksa SERVICE_API_KEYS di .env UI."
      : "API key belum diisi atau salah (401). Isi lewat tombol API key di kanan atas.";
  }
  if (status === 502 || status === 503 || status === 504) {
    return `Service tidak terjangkau dari UI (${status}). Pastikan service sedang jalan dan alamatnya benar di SERVICE_TARGETS.`;
  }
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ");
  }
  if (typeof detail === "string") return detail;
  if (text && text.length < 300 && !text.trim().startsWith("<")) return text;
  return `Permintaan gagal (HTTP ${status}).`;
}

export async function request(method, path, body) {
  if (!target) throw new ApiError(0, "Belum ada service yang dipilih.", null);
  const headers = { Accept: "application/json" };
  const key = storedKey();
  if (key) headers["x-api-key"] = key;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response;
  try {
    response = await fetch(`${target.base}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, describe(0), null);
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (response.ok) return data;
  // 409 carries a full validation result (problems, warnings), not a failure.
  if (response.status === 409 && data && Array.isArray(data.problems)) return { ...data, httpStatus: 409 };
  throw new ApiError(response.status, describe(response.status, data, text), data);
}

export const api = {
  rules: () => request("GET", "/api/v1/config/rules"),
  grade: (gradeId) => request("GET", `/api/v1/config/rules/${gradeId}`),
  patchGrade: (gradeId, body) => request("PATCH", `/api/v1/config/rules/${gradeId}`, body),
  patchGlobal: (body) => request("PATCH", "/api/v1/config/global", body),
  history: ({ gradeId, limit } = {}) => {
    const params = new URLSearchParams();
    if (gradeId) params.set("gradeId", gradeId);
    params.set("limit", limit || 50);
    return request("GET", `/api/v1/config/history?${params}`);
  },
  version: (version) => request("GET", `/api/v1/config/versions/${encodeURIComponent(version)}`),
};
