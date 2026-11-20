import type { Role } from "@/lib/api/types";

const ROLE_RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };

export const KNOWN_ROLES: Role[] = ["viewer", "operator", "admin"];

export function isRole(value: string): value is Role {
  return (KNOWN_ROLES as string[]).includes(value);
}

export function highestRole(roles: string[]): Role {
  let best: Role = "viewer";
  for (const role of roles) {
    if (isRole(role) && ROLE_RANK[role] > ROLE_RANK[best]) best = role;
  }
  return best;
}

export function hasMinimumRole(role: Role, minimum: Role): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minimum];
}

/** Editing controls mirror the backend's `require_operator` policy. */
export function canOperate(role: Role): boolean {
  return hasMinimumRole(role, "operator");
}

export function canAdminister(role: Role): boolean {
  return hasMinimumRole(role, "admin");
}

/** Decode JWT claims for UI display only; the backend verifies the signature. */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const segment = token.split(".")[1];
    if (!segment) return null;
    const normalized = segment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), "=");
    const decoded = atob(padded);
    const bytes = Uint8Array.from(decoded, (char) => char.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function extractRoles(token: string, claim: string): Role[] {
  const payload = decodeJwtPayload(token);
  if (!payload) return [];
  const raw = payload[claim] ?? payload.roles ?? payload.role;
  const values = Array.isArray(raw) ? raw : typeof raw === "string" ? raw.split(/[\s,]+/) : [];
  return values.filter((item): item is Role => typeof item === "string" && isRole(item));
}
