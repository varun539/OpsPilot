import { AlertTriangle, CheckCircle2, FileText, ShieldQuestion } from "lucide-react";

import { SeverityBadge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/feedback";
import { JsonViewer } from "@/components/ui/json-viewer";
import type { InvestigationReport } from "@/lib/api/types";
import { titleCase } from "@/lib/format";

function asReport(value: Record<string, unknown> | null): InvestigationReport | null {
  if (!value) return null;
  const nested = value.report;
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    const report = nested as InvestigationReport;
    // The persisted envelope (RAGResponse) carries citations and provenance
    // beside the structured report. Merge them so source metadata stays
    // accessible instead of being dropped when unwrapping.
    return {
      ...report,
      provider: report.provider ?? (value.provider as string | undefined),
      model: report.model ?? (value.model as string | undefined),
      generation_status:
        report.generation_status ?? (value.generation_status as string | undefined),
      citation_valid:
        report.citation_valid ?? (value.citation_valid as boolean | undefined),
      retrieval_metadata:
        report.retrieval_metadata ??
        (value.retrieval_metadata as Record<string, unknown> | null | undefined),
      source_references:
        report.source_references ??
        (value.source_references as Array<Record<string, unknown>> | undefined),
    };
  }
  return value as InvestigationReport;
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
        {icon}
        {title}
      </h3>
      {children}
    </section>
  );
}

export function InvestigationReportView({
  report,
  summary,
  suspectedRootCause,
  severity,
}: {
  report: Record<string, unknown> | null;
  summary: string | null;
  suspectedRootCause: string | null;
  severity?: string;
}) {
  const parsed = asReport(report);

  if (!parsed && !summary) {
    return (
      <Card>
        <EmptyState
          icon={<FileText className="h-5 w-5" aria-hidden="true" />}
          title="No report available yet"
          description="The structured investigation report appears here after the workflow produces it."
        />
      </Card>
    );
  }

  const hypotheses = parsed?.root_cause_hypotheses ?? [];
  const evidence = parsed?.supporting_evidence ?? [];
  const actions = parsed?.recommended_actions ?? [];
  const references = parsed?.source_references ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Executive summary"
          description="Agent assessment based on the evidence supplied to the workflow"
          action={parsed?.evidence_status ? (
            <span className="inline-flex items-center gap-1 text-[11px] text-muted">
              {parsed.evidence_status === "sufficient" ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-[#86efac]" aria-hidden="true" />
              ) : (
                <ShieldQuestion className="h-3.5 w-3.5 text-[#fcd34d]" aria-hidden="true" />
              )}
              Evidence: {titleCase(parsed.evidence_status)}
            </span>
          ) : undefined}
        />
        <CardBody className="space-y-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-[#d6e0ee]">
            {parsed?.summary ?? summary}
          </p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
            {parsed?.incident_category ? <span>Category: {parsed.incident_category}</span> : null}
            {severity ? (
              <span className="inline-flex items-center gap-1">
                Incident severity: <SeverityBadge severity={severity} />
              </span>
            ) : null}
            {parsed?.affected_components?.length ? (
              <span>Affected: {parsed.affected_components.join(", ")}</span>
            ) : null}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Suspected root cause" />
        <CardBody>
          <p className="mb-3 rounded border border-[#54411b] bg-[#251f10] px-3 py-2 text-[11px] text-[#fcd34d]">
            <AlertTriangle className="mr-1 inline h-3 w-3" aria-hidden="true" />
            These are hypotheses tied to retrieved evidence, not confirmed diagnoses.
          </p>
          {hypotheses.length === 0 ? (
            <p className="text-xs text-muted">
              {suspectedRootCause ?? "No root-cause hypothesis was produced with the available evidence."}
            </p>
          ) : (
            <ul className="space-y-3">
              {hypotheses.map((hypothesis, index) => (
                <li key={`${hypothesis.title}-${index}`} className="rounded border border-border bg-background px-3 py-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-medium text-foreground">{hypothesis.title}</h4>
                    {hypothesis.evidence_strength ? (
                      <span className="text-[11px] text-muted">
                        Evidence strength: {titleCase(hypothesis.evidence_strength)}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-[#c3d0e3]">
                    {hypothesis.explanation}
                  </p>
                  {hypothesis.supporting_chunk_ids?.length ? (
                    <p className="mt-1.5 font-mono text-[10px] text-muted">
                      Sources: {hypothesis.supporting_chunk_ids.join(", ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Findings and supporting evidence" />
          <CardBody>
            {evidence.length === 0 ? (
              <p className="text-xs text-muted">No supporting evidence references were included in the report.</p>
            ) : (
              <ul className="space-y-2">
                {evidence.map((item, index) => {
                  if (typeof item === "string") {
                    return (
                      <li key={`${item}-${index}`} className="rounded border border-border bg-background px-3 py-2 font-mono text-[11px] text-[#c3d0e3]">
                        {item}
                      </li>
                    );
                  }
                  return (
                    <li key={index} className="rounded border border-border bg-background px-3 py-2">
                      <p className="font-mono text-[11px] text-accent">
                        {String(item.chunk_id ?? "unknown chunk")}
                      </p>
                      {item.title ? <p className="text-xs text-foreground">{String(item.title)}</p> : null}
                      {item.reason ? <p className="mt-0.5 text-[11px] text-muted">{String(item.reason)}</p> : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Recommended actions" description="Never executed automatically; human review required" />
          <CardBody>
            {actions.length === 0 ? (
              <p className="text-xs text-muted">No recommended actions were produced.</p>
            ) : (
              <ol className="space-y-2">
                {actions.map((action, index) => (
                  <li key={index} className="rounded border border-border bg-background px-3 py-2">
                    <p className="text-xs font-medium text-foreground">
                      {index + 1}. {action.description}
                    </p>
                    <p className="mt-0.5 text-[11px] text-muted">{action.rationale}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wide text-muted">
                      {action.risk_level ? <span>Risk: {action.risk_level}</span> : null}
                      {action.requires_human_review ? <span>Human review required</span> : null}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Missing information and limitations" />
          <CardBody className="space-y-3">
            <Section title="Missing information">
              {parsed?.missing_information?.length ? (
                <ul className="list-inside list-disc space-y-1 text-xs text-[#c3d0e3]">
                  {parsed.missing_information.map((item) => <li key={item}>{item}</li>)}
                </ul>
              ) : (
                <p className="text-xs text-muted">None reported.</p>
              )}
            </Section>
            <Section title="Limitations">
              {parsed?.limitations?.length ? (
                <ul className="list-inside list-disc space-y-1 text-xs text-[#c3d0e3]">
                  {parsed.limitations.map((item) => <li key={item}>{item}</li>)}
                </ul>
              ) : (
                <p className="text-xs text-muted">None reported.</p>
              )}
            </Section>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Citations and sources" description="Retrieved knowledge-base references preserved from the report" />
          <CardBody>
            {references.length === 0 ? (
              <p className="text-xs text-muted">
                No source references were attached. Evidence chunks are available in the evidence panel.
              </p>
            ) : (
              <ul className="space-y-2">
                {references.map((reference, index) => (
                  <li key={index} className="rounded border border-border bg-background px-3 py-2">
                    <p className="text-xs text-foreground">
                      {String(reference.title ?? reference.chunk_id ?? `Source ${index + 1}`)}
                    </p>
                    <p className="mt-0.5 font-mono text-[10px] text-muted">
                      {String(reference.chunk_id ?? "")}
                      {reference.source_path ? ` · ${String(reference.source_path)}` : ""}
                    </p>
                    <div className="mt-1.5">
                      <JsonViewer value={reference} label="Reference metadata" />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      {parsed ? (
        <Card>
          <CardHeader title="Report provenance" description="Provider and model metadata recorded with the report" />
          <CardBody>
            <JsonViewer
              value={{
                provider: parsed.provider,
                model: parsed.model,
                generation_status: parsed.generation_status,
                citation_valid: parsed.citation_valid,
                retrieval_metadata: parsed.retrieval_metadata,
              }}
              label="Provenance metadata"
            />
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}
