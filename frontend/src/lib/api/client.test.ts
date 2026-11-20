import { describe, expect, it } from "vitest";

import {
  ApiError,
  apiRequest,
  errorMessage,
  setAccessTokenGetter,
  setUnauthorizedHandler,
} from "@/lib/api/client";

describe("api client", () => {
  it("normalizes 404 errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({ error: { code: "not_found", message: "record not found", request_id: "req-1" } }),
            { status: 404, headers: { "Content-Type": "application/json" } },
          ),
        ),
      ),
    );

    await expect(apiRequest("/api/v1/incidents/missing")).rejects.toBeInstanceOf(ApiError);
    try {
      await apiRequest("/api/v1/incidents/missing");
    } catch (error) {
      expect((error as ApiError).status).toBe(404);
      expect((error as ApiError).code).toBe("not_found");
      expect((error as ApiError).requestId).toBe("req-1");
    }
  });

  it("attaches bearer tokens when a getter is configured", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    setAccessTokenGetter(() => "test-token");

    await apiRequest<{ ok: boolean }>("/api/v1/health");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/health"),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
      }),
    );
  });

  it("returns a user-facing message for forbidden errors", () => {
    expect(errorMessage(new ApiError("no", 403, "forbidden"))).toBe(
      "You do not have permission to perform this action.",
    );
  });

  it("notifies the session handler exactly once on 401", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "unauthorized" } }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(apiRequest("/api/v1/incidents")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    setUnauthorizedHandler(null);
  });
});
