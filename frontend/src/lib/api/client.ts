/**
 * Centralized typed HTTP client for the OpsPilot REST API.
 *
 * All requests go through `apiRequest`, which attaches the OIDC access token,
 * normalizes FastAPI error envelopes, and never logs tokens.
 */

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_OPSPILOT_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor(message: string, status: number, code: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Your session is not authorized. Please sign in again.";
    if (error.status === 403) return "You do not have permission to perform this action.";
    if (error.status === 404) return "The requested record was not found.";
    if (error.status === 409) return error.message;
    if (error.status === 422) return "Some fields are invalid. Review the form and try again.";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "An unexpected error occurred.";
}

type TokenGetter = () => Promise<string | null> | string | null;

let tokenGetter: TokenGetter = () => null;
let unauthorizedHandler: (() => void) | null = null;

/** Registered once by the auth provider. */
export function setAccessTokenGetter(getter: TokenGetter): void {
  tokenGetter = getter;
}

/**
 * Registered by the auth provider so a `401` from any request clears the local
 * session and returns the user to the sign-in screen instead of leaving stale
 * protected data visible.
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function toQuery(params: Record<string, string | number | undefined | null>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const serialized = query.toString();
  return serialized ? `?${serialized}` : "";
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH";
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; request_id?: string };
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = await tokenGetter();
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...options.headers,
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    cache: "no-store",
  });

  if (!response.ok) {
    if (response.status === 401) {
      try {
        unauthorizedHandler?.();
      } catch {
        // Session cleanup must never mask the original API error.
      }
    }
    let envelope: ErrorEnvelope = {};
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      // Non-JSON error responses are normalized below.
    }
    const code = envelope.error?.code ?? `http_${response.status}`;
    const message =
      envelope.error?.message ??
      (response.status === 401
        ? "Authentication required"
        : response.status === 403
          ? "Insufficient permissions"
          : response.status === 409
            ? "The request conflicts with the current state"
            : `Request failed with status ${response.status}`);
    throw new ApiError(message, response.status, code, envelope.error?.request_id);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
