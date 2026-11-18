"use client";

import { useAuth } from "@/components/layout/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui/card";
import { authConfig, devAuthEnabled, oidcConfigured } from "@/lib/auth/config";
import { API_BASE_URL } from "@/lib/api/client";

const PERMISSIONS = [
  { label: "View incidents, tickets, investigations, evidence", viewer: true, operator: true, admin: true },
  { label: "Read approval history", viewer: true, operator: true, admin: true },
  { label: "Create incidents", viewer: false, operator: true, admin: true },
  { label: "Start AI investigations", viewer: false, operator: true, admin: true },
  { label: "Create and manage tickets", viewer: false, operator: true, admin: true },
  { label: "Approve or reject investigations", viewer: false, operator: true, admin: true },
];

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-border py-2 last:border-b-0 sm:grid-cols-[12rem_1fr] sm:gap-3">
      <dt className="text-[11px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className="break-words font-mono text-[11px] text-[#c3d0e3]">{value}</dd>
    </div>
  );
}

export default function SettingsPage() {
  const { user, role } = useAuth();

  const mode = devAuthEnabled
    ? "Development authentication (explicit opt-in)"
    : oidcConfigured
      ? "OIDC authorization code + PKCE"
      : "Not configured";

  return (
    <div className="space-y-4">
      <PageHeader
        title="Settings"
        description="Read-only view of the frontend connection, authentication, and role configuration."
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Current session" description="Identity comes from the access token; the backend remains authoritative" />
          <CardBody>
            <dl>
              <Row label="Subject" value={user?.subject ?? "—"} />
              <Row label="Display name" value={user?.name ?? "—"} />
              <Row label="Email" value={user?.email ?? "—"} />
              <Row
                label="Roles"
                value={
                  <span className="flex flex-wrap gap-1">
                    {(user?.roles.length ? user.roles : ["viewer"]).map((item) => (
                      <Badge key={item}>{item}</Badge>
                    ))}
                  </span>
                }
              />
              <Row label="Effective role" value={role} />
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Authentication" description="Public OIDC configuration" />
          <CardBody>
            <dl>
              <Row label="Mode" value={mode} />
              <Row label="Authority" value={authConfig.authority || "Not set"} />
              <Row label="Client ID" value={authConfig.clientId || "Not set"} />
              <Row label="Redirect URI" value={authConfig.redirectUri || "Not set"} />
              <Row label="Scope" value={authConfig.scope} />
              <Row label="Roles claim" value={authConfig.rolesClaim} />
            </dl>
            <p className="mt-3 text-[11px] leading-relaxed text-muted">
              Access tokens are held in session-scoped storage by the OIDC client and sent
              as bearer tokens. ID tokens are never used as API credentials. No client
              secret is present in the frontend.
            </p>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="API connection" />
        <CardBody>
          <dl>
            <Row label="API base URL" value={API_BASE_URL} />
            <Row
              label="OpenAPI docs"
              value={
                <a href={`${API_BASE_URL}/docs`} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                  {API_BASE_URL}/docs
                </a>
              }
            />
          </dl>
          <p className="mt-3 text-[11px] leading-relaxed text-muted">
            The backend must allow this frontend origin in
            <code className="mx-1 rounded bg-surface-raised px-1">OPSPILOT_CORS_ORIGINS</code>.
            For local development that is typically
            <code className="mx-1 rounded bg-surface-raised px-1">http://localhost:3000</code>.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Role permissions" description="Usability only — every operation is authorized by the API" />
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">Capability</th>
                <th scope="col" className="px-4 py-2 font-medium">Viewer</th>
                <th scope="col" className="px-4 py-2 font-medium">Operator</th>
                <th scope="col" className="px-4 py-2 font-medium">Admin</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {PERMISSIONS.map((permission) => (
                <tr key={permission.label}>
                  <td className="px-4 py-2 text-[#c3d0e3]">{permission.label}</td>
                  {(["viewer", "operator", "admin"] as const).map((key) => (
                    <td key={key} className="px-4 py-2">
                      {permission[key] ? (
                        <span className="text-[#86efac]" aria-label="Allowed">Yes</span>
                      ) : (
                        <span className="text-muted" aria-label="Not allowed">No</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
