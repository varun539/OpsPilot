import { apiRequest, toQuery } from "./client";
import type { Ticket, TicketCreateRequest, TicketListParams } from "./types";

export function listTickets(params: TicketListParams = {}, signal?: AbortSignal) {
  return apiRequest<Ticket[]>(`/api/v1/tickets${toQuery({ ...params })}`, { signal });
}

export function getTicket(ticketId: string, signal?: AbortSignal) {
  return apiRequest<Ticket>(`/api/v1/tickets/${encodeURIComponent(ticketId)}`, { signal });
}

export function updateTicket(
  ticketId: string,
  payload: Partial<TicketCreateRequest> & { status?: string },
) {
  return apiRequest<Ticket>(`/api/v1/tickets/${encodeURIComponent(ticketId)}`, {
    method: "PATCH",
    body: payload,
  });
}

export function closeTicket(ticketId: string) {
  return apiRequest<Ticket>(`/api/v1/tickets/${encodeURIComponent(ticketId)}/close`, {
    method: "POST",
  });
}

export type { TicketCreateRequest };
