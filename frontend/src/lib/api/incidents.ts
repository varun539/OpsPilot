import { apiRequest, toQuery } from "./client";
import type {
  Approval,
  Incident,
  IncidentCreateRequest,
  IncidentHistory,
  IncidentListParams,
  Investigation,
  Resolution,
  Ticket,
  TicketCreateRequest,
} from "./types";

export function listIncidents(params: IncidentListParams = {}, signal?: AbortSignal) {
  return apiRequest<Incident[]>(
    `/api/v1/incidents${toQuery({ ...params })}`,
    { signal },
  );
}

export function getIncident(incidentId: string, signal?: AbortSignal) {
  return apiRequest<Incident>(`/api/v1/incidents/${encodeURIComponent(incidentId)}`, {
    signal,
  });
}

export function createIncident(payload: IncidentCreateRequest) {
  return apiRequest<Incident>("/api/v1/incidents", { method: "POST", body: payload });
}

export function updateIncident(incidentId: string, payload: Partial<IncidentCreateRequest>) {
  return apiRequest<Incident>(`/api/v1/incidents/${encodeURIComponent(incidentId)}`, {
    method: "PATCH",
    body: payload,
  });
}

export function getIncidentHistory(incidentId: string, signal?: AbortSignal) {
  return apiRequest<IncidentHistory>(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/history`,
    { signal },
  );
}

export function listIncidentTickets(incidentId: string, signal?: AbortSignal) {
  return apiRequest<Ticket[]>(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/tickets`,
    { signal },
  );
}

export function createIncidentTicket(incidentId: string, payload: TicketCreateRequest) {
  return apiRequest<Ticket>(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/tickets`,
    { method: "POST", body: payload },
  );
}

export function listIncidentInvestigations(incidentId: string, signal?: AbortSignal) {
  return apiRequest<Investigation[]>(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/investigations`,
    { signal },
  );
}

export function listIncidentApprovals(incidentId: string, signal?: AbortSignal) {
  return apiRequest<Approval[]>(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/approvals`,
    { signal },
  );
}

export function listIncidentResolutions(incidentId: string, signal?: AbortSignal) {
  return apiRequest<Resolution[]>(
    `/api/v1/incidents/${encodeURIComponent(incidentId)}/resolutions`,
    { signal },
  );
}
