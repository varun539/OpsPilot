import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConfigRequiredScreen } from "@/components/layout/auth-screens";

describe("ConfigRequiredScreen", () => {
  it("lists the public OIDC variables required for authentication", () => {
    render(<ConfigRequiredScreen />);

    expect(
      screen.getByRole("heading", {
        name: "Authentication configuration required",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("NEXT_PUBLIC_OPSPILOT_OIDC_AUTHORITY"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("NEXT_PUBLIC_OPSPILOT_OIDC_CLIENT_ID"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/NEXT_PUBLIC_OPSPILOT_DEV_AUTH=true/),
    ).toBeInTheDocument();
  });
});
