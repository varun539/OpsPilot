"use client";

import Link from "next/link";
import {
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  ClipboardList,
  Plus,
  ShieldCheck,
} from "lucide-react";

import { BreakdownChart } from "@/components/dashboard/breakdown-chart";
import { KpiCard } from "@/components/dashboard/kpi-card";
import { RecentIncidents, RecentInvestigations } from "@/components/dashboard/recent-activity";
import { Button } from "@/components/ui/button";
import { Card, CardBody, PageHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { getDashboardStats } from "@/lib/api/approvals";
import { listIncidents } from "@/lib/api/incidents";
import { listInvestigations } from "@/lib/api/investigations";
import { useAuth } from "@/components/layout/auth-provider";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { canOperate } from "@/lib/auth/roles";

export default function DashboardPage() {
  const { role } = useAuth();
  const stats = useApiResource((signal) => getDashboardStats(signal), "dashboard-stats");
  const incidents = useApiResource(
    (signal) => listIncidents({ limit: 6 }, signal),
    "dashboard-incidents",
  );
  const investigations = useApiResource(
    (signal) => listInvestigations({ limit: 6 }, signal),
    "dashboard-investigations",
  );

  if (stats.error) {
    return (
      <div className="space-y-6">
        <PageHeader title="Operations overview" />
        <Card>
          <ErrorState message={stats.error} onRetry={stats.refresh} />
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Operations overview"
        description="Live incident, investigation, and approval state from the OpsPilot API."
        action={
          canOperate(role) ? (
            <Link href="/incidents?new=1">
              <Button variant="primary">
                <Plus className="h-4 w-4" aria-hidden="true" />
                New incident
              </Button>
            </Link>
          ) : null
        }
      />

      <section aria-label="Key metrics" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="Total incidents"
          value={stats.data?.total_incidents}
          loading={stats.loading}
          icon={<ClipboardList className="h-4 w-4" />}
        />
        <KpiCard
          label="Open"
          value={stats.data?.open_incidents}
          loading={stats.loading}
          icon={<AlertTriangle className="h-4 w-4" />}
          tone="accent"
        />
        <KpiCard
          label="Investigating"
          value={stats.data?.investigating_incidents}
          loading={stats.loading}
          icon={<BrainCircuit className="h-4 w-4" />}
          tone="accent"
        />
        <KpiCard
          label="Awaiting approval"
          value={stats.data?.awaiting_approval_incidents}
          loading={stats.loading}
          icon={<ShieldCheck className="h-4 w-4" />}
          tone="warning"
        />
        <KpiCard
          label="Completed investigations"
          value={stats.data?.completed_investigations}
          loading={stats.loading}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tone="success"
        />
        <KpiCard
          label="Open tickets"
          value={stats.data?.open_tickets}
          loading={stats.loading}
          icon={<ClipboardList className="h-4 w-4" />}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardBody>
            {stats.loading ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : (
              <BreakdownChart
                title="Incidents by severity"
                data={stats.data?.incidents_by_severity ?? []}
              />
            )}
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            {stats.loading ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : (
              <BreakdownChart
                title="Incidents by status"
                data={stats.data?.incidents_by_status ?? []}
              />
            )}
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            {stats.loading ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : (
              <BreakdownChart
                title="Investigation outcomes"
                data={stats.data?.investigations_by_workflow_status ?? []}
                emptyLabel="No investigations have run yet"
              />
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <RecentIncidents incidents={incidents.data ?? []} loading={incidents.loading} />
        <RecentInvestigations
          investigations={investigations.data ?? []}
          loading={investigations.loading}
        />
      </div>
    </div>
  );
}
