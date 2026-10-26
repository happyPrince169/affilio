import type {
  OrderItemInput,
  PaginatedOrders,
  ProcurementStats,
  ProductProcurementSummary,
  PurchaseOrderCreate,
  PurchaseOrderDetail,
  PurchaseOrderStatus,
  PurchaseOrderUpdate,
  PurchaseRequest,
  PurchaseRequestCreate,
  PurchaseRequestDecision,
  PurchaseRequestStatus,
  ReceiveRequest,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const BASE = `${API_V1_PREFIX}/procurement`;
const JSON_HEADERS = { "Content-Type": "application/json" };
const post = <T>(path: string, body: unknown, method = "POST") =>
  apiFetch<T>(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

export interface OrderFilters {
  status?: PurchaseOrderStatus;
  q?: string;
  page?: number;
  page_size?: number;
}

function qs(params: object): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params as Record<string, unknown>))
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const fetchProcurementStats = () => apiFetch<ProcurementStats>(`${BASE}/stats`);
export const fetchRequests = (status?: PurchaseRequestStatus, productId?: string) =>
  apiFetch<PurchaseRequest[]>(`${BASE}/requests${qs({ status, product_id: productId })}`);
export const createRequest = (body: PurchaseRequestCreate) =>
  post<PurchaseRequest>(`${BASE}/requests`, body);
export const decideRequest = (id: string, body: PurchaseRequestDecision) =>
  post<PurchaseRequest>(`${BASE}/requests/${encodeURIComponent(id)}/decision`, body);
export const fetchOrders = (filters: OrderFilters) =>
  apiFetch<PaginatedOrders>(`${BASE}/orders${qs(filters)}`);
export const fetchOrder = (id: string) =>
  apiFetch<PurchaseOrderDetail>(`${BASE}/orders/${encodeURIComponent(id)}`);
export const createOrder = (body: PurchaseOrderCreate) =>
  post<PurchaseOrderDetail>(`${BASE}/orders`, body);
export const updateOrder = (id: string, body: PurchaseOrderUpdate) =>
  post<PurchaseOrderDetail>(`${BASE}/orders/${encodeURIComponent(id)}`, body, "PATCH");
export const changeOrderStatus = (id: string, status: PurchaseOrderStatus, note?: string) =>
  post<PurchaseOrderDetail>(`${BASE}/orders/${encodeURIComponent(id)}/status`, {
    status,
    note: note || null,
  });
export const addOrderItem = (id: string, body: OrderItemInput) =>
  post<PurchaseOrderDetail>(`${BASE}/orders/${encodeURIComponent(id)}/items`, body);
export const removeOrderItem = (id: string, itemId: string) =>
  apiFetch<PurchaseOrderDetail>(
    `${BASE}/orders/${encodeURIComponent(id)}/items/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
  );
export const receiveOrder = (id: string, body: ReceiveRequest) =>
  post<PurchaseOrderDetail>(`${BASE}/orders/${encodeURIComponent(id)}/receive`, body);
export const fetchProductProcurement = (productId: string) =>
  apiFetch<ProductProcurementSummary>(`${BASE}/products/${encodeURIComponent(productId)}`);

export const procurementQueries = {
  all: () => ["procurement"] as const,
  stats: () =>
    queryOptions({
      queryKey: [...procurementQueries.all(), "stats"] as const,
      queryFn: fetchProcurementStats,
      staleTime: 5_000,
    }),
  requests: (status?: PurchaseRequestStatus, productId?: string) =>
    queryOptions({
      queryKey: [
        ...procurementQueries.all(),
        "requests",
        status ?? "all",
        productId ?? "all",
      ] as const,
      queryFn: () => fetchRequests(status, productId),
      staleTime: 5_000,
    }),
  orders: (filters: OrderFilters) =>
    queryOptions({
      queryKey: [...procurementQueries.all(), "orders", filters] as const,
      queryFn: () => fetchOrders(filters),
      staleTime: 5_000,
    }),
  order: (id: string) =>
    queryOptions({
      queryKey: [...procurementQueries.all(), "order", id] as const,
      queryFn: () => fetchOrder(id),
      staleTime: 5_000,
    }),
  product: (productId: string) =>
    queryOptions({
      queryKey: [...procurementQueries.all(), "product", productId] as const,
      queryFn: () => fetchProductProcurement(productId),
      staleTime: 5_000,
    }),
};
