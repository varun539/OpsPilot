// Frontend API types are generated from the FastAPI OpenAPI schema.
// Source of truth: backend FastAPI routes and Pydantic models.
// Regenerate with: npm run api:types

import type { components } from "./generated";

export type Incident = components["schemas"]["Incident"];
export type IncidentCreate = components["schemas"]["IncidentCreate"];
export type IncidentUpdate = components["schemas"]["IncidentUpdate"];

export type Ticket = components["schemas"]["Ticket"];
export type TicketCreateRequest = components["schemas"]["TicketCreateRequest"];
export type TicketUpdate = components["schemas"]["TicketUpdate"];

export type Investigation = components["schemas"]["Investigation"];
export type InvestigationStartRequest = components["schemas"]["InvestigationStartRequest"];
export type InvestigationStartResponse = components["schemas"]["InvestigationStartResponse"];
export type InvestigationApprovalRequest = components["schemas"]["InvestigationApprovalRequest"];
export type InvestigationApprovalResponse = components["schemas"]["InvestigationApprovalResponse"];

export type Evidence = components["schemas"]["Evidence"];
export type Approval = components["schemas"]["Approval"];
export type ApprovalQueueItem = components["schemas"]["ApprovalQueueItem"];
export type Resolution = components["schemas"]["Resolution"];

export type DashboardStats = components["schemas"]["DashboardStats"];
export type BreakdownItem = components["schemas"]["BreakdownItem"];

// Frontend-only UI helper types.
export type Role = "viewer" | "operator" | "admin";

export type IncidentCreateRequest = IncidentCreate;

export interface IncidentListParams {
  severity?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface InvestigationListParams {
  status?: string;
  workflow_status?: string;
  limit?: number;
  offset?: number;
}

export interface TicketListParams {
  status?: string;
  priority?: string;
  limit?: number;
  offset?: number;
}

export interface ApprovalListParams {
  status?: string;
  limit?: number;
  offset?: number;
}

export interface InvestigationReport {
  summary?: string;
  incident_category?: string;
  evidence_status?: string;
  affected_components?: string[];
  root_cause_hypotheses?: Array<{
    title?: string;
    explanation?: string;
    supporting_chunk_ids?: string[];
    evidence_strength?: string;
  }>;
  recommended_actions?: Array<{
    description?: string;
    rationale?: string;
    risk_level?: string;
    requires_human_review?: boolean;
  }>;
  missing_information?: string[];
  limitations?: string[];
  citations?: Array<Record<string, unknown>>;
  supporting_evidence?: Array<string | Record<string, unknown>>;
  source_references?: Array<Record<string, unknown>>;
  provider?: string;
  model?: string;
  generation_status?: string;
  citation_valid?: boolean;
  retrieval_metadata?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export type IncidentStatus = Incident["status"];
export type Severity = Incident["severity"];
export type Priority = Incident["priority"];
export type TicketStatus = Ticket["status"];
export type TicketPriority = Ticket["priority"];
export type WorkflowStatus = Investigation["workflow_status"];
export type ApprovalStatus = Approval["status"];

export type IncidentHistory = {
  incident: Incident;
  investigations: Investigation[];
  tickets: Ticket[];
  approvals: Approval[];
  resolutions: Resolution[];
  events: Array<{
    id: string;
    entity_type: string;
    entity_id: string;
    event_type: string;
    actor: string | null;
    old_value: Record<string, unknown> | null;
    new_value: Record<string, unknown> | null;
    created_at: string;
  }>;
};
