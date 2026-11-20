"use client";

import { useState } from "react";
import { Database, FileSearch, GitBranch } from "lucide-react";

import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/feedback";
import { JsonViewer } from "@/components/ui/json-viewer";
import type { Evidence } from "@/lib/api/types";
import { formatDateTime, titleCase } from "@/lib/format";

const TYPE_ICONS: Record<string, React.ReactNode> = {
  diagnostic: <FileSearch className="h-3.5 w-3.5" aria-hidden="true" />,
  knowledge_base: <Database className="h-3.5 w-3.5" aria-hidden="true" />,
  correlation: <GitBranch className="h-3.5 w-3.5" aria-hidden="true" />,
};

export function EvidenceViewer({ evidence }: { evidence: Evidence[] }) {
  const [filter, setFilter] = useState<string>("all");
  const types = Array.from(new Set(evidence.map((item) => item.evidence_type)));
  const visible = filter === "all" ? evidence : evidence.filter((item) => item.evidence_type === filter);

  return (
    <Card>
      <CardHeader
        title="Investigation evidence"
        description={`${evidence.length} record${evidence.length === 1 ? "" : "s"} stored by the orchestration service`}
      />
      {evidence.length === 0 ? (
        <EmptyState
          icon={<FileSearch className="h-5 w-5" aria-hidden="true" />}
          title="No evidence recorded"
          description="Diagnostic results, retrieved knowledge-base chunks, and correlation output will appear here."
        />
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2">
            <button
              type="button"
              onClick={() => setFilter("all")}
              aria-pressed={filter === "all"}
              className={`rounded border px-2 py-1 text-[11px] ${
                filter === "all"
                  ? "border-border-strong bg-surface-raised text-foreground"
                  : "border-transparent text-muted hover:text-foreground"
              }`}
            >
              All ({evidence.length})
            </button>
            {types.map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setFilter(type)}
                aria-pressed={filter === type}
                className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] ${
                  filter === type
                    ? "border-border-strong bg-surface-raised text-foreground"
                    : "border-transparent text-muted hover:text-foreground"
                }`}
              >
                {TYPE_ICONS[type]}
                {titleCase(type)} ({evidence.filter((item) => item.evidence_type === type).length})
              </button>
            ))}
          </div>
          <CardBody className="space-y-2">
            {visible.map((item) => (
              <details key={item.id} className="group rounded border border-border bg-background">
                <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs text-foreground">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="text-muted">{TYPE_ICONS[item.evidence_type]}</span>
                    {titleCase(item.evidence_type)}
                    <span className="font-mono text-[10px] text-muted">{item.source ?? item.id}</span>
                  </span>
                  <span className="text-[10px] text-muted">{formatDateTime(item.created_at)}</span>
                </summary>
                <div className="space-y-2 border-t border-border px-3 py-2.5">
                  {typeof item.content === "string" ? (
                    <pre className="max-h-72 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[#c3d0e3]">
                      {item.content}
                    </pre>
                  ) : (
                    <JsonViewer value={item.content} label="Evidence content" defaultOpen />
                  )}
                  {Object.keys(item.metadata ?? {}).length > 0 ? (
                    <JsonViewer value={item.metadata} label="Evidence metadata" />
                  ) : null}
                </div>
              </details>
            ))}
          </CardBody>
        </>
      )}
    </Card>
  );
}
