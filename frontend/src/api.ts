import type { Transaction } from "./types";

// Dev serves through the Vite proxy (/api -> 127.0.0.1:8000); a production
// build can sit behind the same reverse proxy, so one base is enough.
const BASE = "/api";

async function call<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "X-Analyst-Token": token, ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.error?.message ?? `${response.status} ${response.statusText}`;
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function listTransactions(token: string, status?: string): Promise<Transaction[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return call<Transaction[]>(`/transactions${query}`, token);
}

export function approve(token: string, id: string): Promise<Transaction> {
  return call<Transaction>(`/transactions/${id}/approve`, token, { method: "POST" });
}

export function reject(token: string, id: string): Promise<Transaction> {
  return call<Transaction>(`/transactions/${id}/reject`, token, { method: "POST" });
}
