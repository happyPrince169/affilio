/** Procurement contracts (M2B). Mirrors `apps/api/src/affilio/modules/procurement/schemas.py`. */

import type { Platform } from "./domain";

export const PURCHASE_REQUEST_STATUSES = [
  "requested",
  "approved",
  "rejected",
  "ordered",
  "cancelled",
] as const;
export type PurchaseRequestStatus = (typeof PURCHASE_REQUEST_STATUSES)[number];

export const PURCHASE_ORDER_STATUSES = [
  "draft",
  "placed",
  "shipped",
  "delivered",
  "partially_received",
  "received",
  "cancelled",
] as const;
export type PurchaseOrderStatus = (typeof PURCHASE_ORDER_STATUSES)[number];

export interface ProductRef {
  id: string;
  name: string;
  status: string;
  platform: Platform;
  product_url: string | null;
}

export interface PurchaseRequest {
  id: string;
  product: ProductRef;
  status: PurchaseRequestStatus;
  quantity: number;
  reason: string | null;
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  order_id: string | null;
  order_number: string | null;
  created_at: string;
  updated_at: string;
}

export interface PurchaseRequestCreate {
  product_id: string;
  quantity?: number;
  reason?: string | null;
}

export interface PurchaseRequestDecision {
  status: "approved" | "rejected" | "cancelled" | "requested";
  note?: string | null;
}

export interface OrderItemInput {
  purchase_request_id?: string | null;
  product_id?: string | null;
  quantity_ordered?: number | null;
  unit_cost?: string | number | null;
  notes?: string | null;
}

export interface PurchaseOrderCreate {
  platform?: Platform;
  supplier_name?: string | null;
  platform_order_ref?: string | null;
  currency?: string | null;
  shipping_cost?: string | number | null;
  tracking_number?: string | null;
  tracking_url?: string | null;
  expected_delivery_at?: string | null;
  notes?: string | null;
  items?: OrderItemInput[];
}

export type PurchaseOrderUpdate = Omit<PurchaseOrderCreate, "items" | "platform">;

export interface PurchaseOrderItem {
  id: string;
  product: ProductRef;
  purchase_request_id: string | null;
  quantity_ordered: number;
  quantity_received: number;
  unit_cost: string | null;
  line_total: string | null;
  received_at: string | null;
  notes: string | null;
}

export interface PurchaseOrderSummary {
  id: string;
  order_number: string;
  status: PurchaseOrderStatus;
  platform: Platform;
  supplier_name: string | null;
  platform_order_ref: string | null;
  currency: string | null;
  shipping_cost: string | null;
  total_cost: string | null;
  tracking_number: string | null;
  tracking_url: string | null;
  ordered_at: string | null;
  expected_delivery_at: string | null;
  delivered_at: string | null;
  item_count: number;
  units_ordered: number;
  units_received: number;
  status_changed_at: string;
  created_at: string;
  updated_at: string;
}

export interface PurchaseOrderDetail extends PurchaseOrderSummary {
  notes: string | null;
  items: PurchaseOrderItem[];
  allowed_transitions: PurchaseOrderStatus[];
  receivable: boolean;
}

export interface PaginatedOrders {
  items: PurchaseOrderSummary[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ProcurementStats {
  requests_by_status: Record<PurchaseRequestStatus, number>;
  orders_by_status: Record<PurchaseOrderStatus, number>;
  units_ordered: number;
  units_received: number;
  open_orders: number;
}

export interface ProductProcurementSummary {
  open_requests: number;
  units_ordered: number;
  units_received: number;
  orders: PurchaseOrderSummary[];
}

export interface ReceiveRequest {
  items: { item_id: string; quantity: number }[];
  note?: string | null;
}
