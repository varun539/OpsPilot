import { apiRequest, toQuery } from "./client";
import type {
  Approval,
  Evidence,
  Investigation,
  InvestigationApprovalRequest,
  InvestigationApprovalResponse,
  InvestigationListParams,
  InvestigationStartRequest,
  InvestigationStartResponse,
} from "./types";

export function listInvestigations(
  params: InvestigationListParams = {},
  signal?: AbortSignal,
) {
  return apiRequest<Investigation[]>(
    `/api/v1/investigations${toQuery({ ...params })}`,
    { signal },
  );
}

export function getInvestigation(investigationId: string, signal?: AbortSignal) {
  return apiRequest<Investigation>(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}`,
    { signal },
  );
}

export function getInvestigationEvidence(investigationId: string, signal?: AbortSignal) {
  return apiRequest<Evidence[]>(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/evidence`,
    { signal },
  );
}

export function startInvestigation(
  incidentId: string,
  payload: InvestigationStartRequest,
) {
  return apiRequest<InvestigationStartResponse>(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/investigations`,
    { method: "POST", body: payload },
  );
}

export function decideInvestigation(
  investigationId: string,
  payload: InvestigationApprovalRequest,
) {
  return apiRequest<InvestigationApprovalResponse>(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/approval`,
    { method: "POST", body: payload },
  );
}

export function getInvestigationApproval(
  investigationId: string,
  signal?: AbortSignal,
) {
  return apiRequest<Approval>(
    `/api/v1/investigations/${encodeURIComponent(investigationId)}/approval`,
    { signal },
  );
}
