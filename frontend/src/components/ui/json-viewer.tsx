"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export function JsonViewer({
  value,
  label = "Structured data",
  defaultOpen = false,
}: {
  value: unknown;
  label?: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const serialized = JSON.stringify(value, null, 2) ?? String(value);

  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs font-medium text-muted hover:text-foreground"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {label}
      </button>
      {open ? (
        <pre className="max-h-80 overflow-auto border-t border-border bg-background px-3 py-2 font-mono text-[11px] leading-relaxed text-[#c3d0e3]">
          {serialized}
        </pre>
      ) : null}
    </div>
  );
}
