import { apiRequest, toQuery } from "./client";
import type {
  ApprovalListParams,
  ApprovalQueueItem,
  DashboardStats,
} from "./types";

export function listApprovals(params: ApprovalListParams = {}, signal?: AbortSignal) {
  return apiRequest<ApprovalQueueItem[]>(
    `/api/v1/approvals${toQuery({ ...params })}`,
    { signal },
  );
}

export function getDashboardStats(signal?: AbortSignal) {
  return apiRequest<DashboardStats>("/api/v1/dashboard/stats", { signal });
}
