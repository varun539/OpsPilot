import { describe, expect, it } from "vitest";

import {
  canOperate,
  decodeJwtPayload,
  extractRoles,
  hasMinimumRole,
  highestRole,
} from "@/lib/auth/roles";

describe("role helpers", () => {
  it("selects the highest available role", () => {
    expect(highestRole(["viewer", "operator"])).toBe("operator");
    expect(highestRole(["admin"])).toBe("admin");
    expect(highestRole([])).toBe("viewer");
  });

  it("compares role rank", () => {
    expect(hasMinimumRole("operator", "viewer")).toBe(true);
    expect(hasMinimumRole("viewer", "operator")).toBe(false);
    expect(canOperate("operator")).toBe(true);
    expect(canOperate("viewer")).toBe(false);
  });

  it("extracts roles from an access token", () => {
    const payload = btoa(JSON.stringify({ roles: ["operator", "unknown"] }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/g, "");
    const token = `header.${payload}.signature`;
    expect(extractRoles(token, "roles")).toEqual(["operator"]);
  });

  it("decodes a padded JWT payload", () => {
    const payload = btoa(JSON.stringify({ sub: "user-1" }));
    const token = `header.${payload}.signature`;
    expect(decodeJwtPayload(token)?.sub).toBe("user-1");
  });
});
