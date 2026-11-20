"use client";

import { useMemo, useState } from "react";
import { Plus, RefreshCw, Search } from "lucide-react";

import { useAuth } from "@/components/layout/auth-provider";
import { CreateIncidentDialog } from "@/components/incidents/create-incident-dialog";
import { IncidentTable } from "@/components/incidents/incident-table";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/field";
import { listIncidents } from "@/lib/api/incidents";
import { canOperate } from "@/lib/auth/roles";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const PAGE_SIZE = 10;
const SEVERITIES = ["", "critical", "high", "medium", "low", "info"];
const STATUSES = ["", "open", "investigating", "awaiting_approval", "resolved", "closed", "reopened"];

export default function IncidentsPage() {
  const { role } = useAuth();
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"desc" | "asc">("desc");
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);

  const query = useMemo(
    () => ({ status: status || undefined, severity: severity || undefined, limit: PAGE_SIZE, offset }),
    [status, severity, offset],
  );

  const incidents = useApiResource(
    (signal) => listIncidents(query, signal),
    JSON.stringify(query),
  );

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = (incidents.data ?? []).filter(
      (incident) =>
        !term ||
        incident.title.toLowerCase().includes(term) ||
        incident.incident_number.toLowerCase().includes(term),
    );
    return [...filtered].sort((a, b) => {
      const comparison = a.created_at.localeCompare(b.created_at);
      return sort === "asc" ? comparison : -comparison;
    });
  }, [incidents.data, search, sort]);

  const hasFilters = Boolean(search || status || severity);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Incidents"
        description="Search and triage recorded incidents. Severity and status filters are applied by the API; text search filters the loaded page."
        action={
          canOperate(role) ? (
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              New incident
            </Button>
          ) : null
        }
      />

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-border px-4 py-3">
          <div className="min-w-[12rem] flex-1">
            <label htmlFor="incident-search" className="mb-1 block text-[11px] text-muted">
              Search loaded incidents
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted" aria-hidden="true" />
              <Input
                id="incident-search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Incident number or title"
                className="pl-8"
              />
            </div>
          </div>
          <div>
            <label htmlFor="severity-filter" className="mb-1 block text-[11px] text-muted">Severity</label>
            <Select
              id="severity-filter"
              value={severity}
              onChange={(event) => {
                setSeverity(event.target.value);
                setOffset(0);
              }}
              className="w-32"
            >
              {SEVERITIES.map((option) => (
                <option key={option || "all"} value={option}>{option || "All"}</option>
              ))}
            </Select>
          </div>
          <div>
            <label htmlFor="status-filter" className="mb-1 block text-[11px] text-muted">Status</label>
            <Select
              id="status-filter"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
              className="w-40"
            >
              {STATUSES.map((option) => (
                <option key={option || "all"} value={option}>{option ? option.replace(/_/g, " ") : "All"}</option>
              ))}
            </Select>
          </div>
          <div>
            <label htmlFor="sort-filter" className="mb-1 block text-[11px] text-muted">Created</label>
            <Select
              id="sort-filter"
              value={sort}
              onChange={(event) => setSort(event.target.value === "asc" ? "asc" : "desc")}
              className="w-36"
            >
              <option value="desc">Newest first</option>
              <option value="asc">Oldest first</option>
            </Select>
          </div>
          <Button onClick={incidents.refresh} disabled={incidents.loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${incidents.loading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </Button>
        </div>

        <CardHeader
          title="Incident queue"
          description={`Page ${Math.floor(offset / PAGE_SIZE) + 1}`}
        />

        {incidents.error ? (
          <ErrorState message={incidents.error} onRetry={incidents.refresh} />
        ) : (
          <IncidentTable
            incidents={visible}
            loading={incidents.loading}
            filtered={hasFilters}
            emptyAction={
              canOperate(role) ? (
                <Button size="sm" onClick={() => setCreateOpen(true)}>
                  <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                  Create incident
                </Button>
              ) : undefined
            }
          />
        )}

        <CardBody className="flex items-center justify-between border-t border-border py-2.5">
          <span className="text-[11px] text-muted">
            {incidents.loading ? "Loading…" : `${visible.length} shown`}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={() => setOffset((current) => Math.max(current - PAGE_SIZE, 0))}
              disabled={offset === 0 || incidents.loading}
            >
              Previous
            </Button>
            <Button
              size="sm"
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
              disabled={incidents.loading || (incidents.data?.length ?? 0) < PAGE_SIZE}
            >
              Next
            </Button>
          </div>
        </CardBody>
      </Card>

      <CreateIncidentDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
