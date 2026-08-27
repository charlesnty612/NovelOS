// API client：薄 fetch 封装，统一错误处理与 JSON 容错解析。
//
// 设计要点：
// - baseURL：相对路径 "/api"，由 vite 开发代理转发到 127.0.0.1:18081（后端）。
// - 错误统一抛 ApiError{status, detail}，便于 UI 统一展示。
// - 后端对 core_json / data_json / time_json 等可能返回字符串或对象；本层做容错：
//   是字符串 → JSON.parse；失败 → 回退原值（字符串形式）。统一在 client 层处理，
//   调用方拿到的就是已解析的对象（不会因序列化差异炸错）。

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`ApiError(${status}): ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
}

const BASE = '/api';

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${BASE}${path}`, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
  }
  return url.pathname + (url.search || '');
}

/**
 * 尝试将后端返回的「可能是字符串也可能是对象」的 JSON 字段解析为对象。
 * - 已是对象：原样返回。
 * - 是字符串且能 parse：返回解析结果。
 * - 是字符串但 parse 失败：返回原字符串（不抛错，由调用方决定如何展示）。
 */
export function coerceJson<T = Record<string, unknown>>(value: unknown): T | string | unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '' || trimmed === 'null') return value;
    if (
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    ) {
      try {
        return JSON.parse(trimmed) as T;
      } catch {
        return value;
      }
    }
    return value;
  }
  return value;
}

async function parseError(resp: Response): Promise<string> {
  const text = await resp.text().catch(() => '');
  if (!text) return resp.statusText || `HTTP ${resp.status}`;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
      return JSON.stringify(detail);
    }
    return text;
  } catch {
    return text;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal } = options;
  const url = buildUrl(path, query);
  const init: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
    signal,
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'network error';
    throw new ApiError(0, msg);
  }
  if (resp.status === 204) {
    return undefined as unknown as T;
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, await parseError(resp));
  }
  const ct = resp.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) {
    return (await resp.json()) as T;
  }
  return (await resp.text()) as unknown as T;
}

// -------------------------------------------------------------- 高阶方法
export const api = {
  get<T>(path: string, query?: RequestOptions['query']): Promise<T> {
    return request<T>(path, { method: 'GET', query });
  },
  post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, { method: 'POST', body });
  },
  put<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, { method: 'PUT', body });
  },
  patch<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, { method: 'PATCH', body });
  },
  delete<T>(path: string): Promise<T> {
    return request<T>(path, { method: 'DELETE' });
  },
};
