"use client";

import type { PurchaseOrderStatus, PurchaseRequestStatus } from "@affilio/shared-types";
import { PURCHASE_ORDER_STATUSES, PURCHASE_REQUEST_STATUSES } from "@affilio/shared-types";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ExternalLink, Plus, ShoppingCart, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { MediaPagination } from "@/components/media/media-pagination";
import { NewOrderDialog } from "@/components/procurement/new-order-dialog";
import {
  ORDER_LABEL,
  OrderStatusBadge,
  REQUEST_LABEL,
  RequestStatusBadge,
} from "@/components/procurement/procurement-badges";
import { DecisionDialog, NewRequestDialog } from "@/components/procurement/request-dialogs";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { procurementQueries } from "@/lib/api/procurement";
import { formatDateTime, formatMoney, formatRelative } from "@/lib/format";

const ALL = "all";

function RequestsTab() {
  const [status, setStatus] = useState<PurchaseRequestStatus | undefined>(undefined);
  const requests = useQuery(procurementQueries.requests(status));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={status ?? ALL}
          onValueChange={(v) => setStatus(v === ALL ? undefined : (v as PurchaseRequestStatus))}
        >
          <SelectTrigger className="w-44" aria-label="Request status filter">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All requests</SelectItem>
            {PURCHASE_REQUEST_STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {REQUEST_LABEL[s]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="ml-auto flex gap-2">
          <NewRequestDialog
            trigger={
              <Button variant="outline" size="sm">
                <Plus /> New request
              </Button>
            }
          />
          <NewOrderDialog
            trigger={
              <Button size="sm">
                <ShoppingCart /> New order from approved
              </Button>
            }
          />
        </div>
      </div>
      {requests.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : requests.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load requests</AlertTitle>
          <AlertDescription>{requests.error.message}</AlertDescription>
        </Alert>
      ) : requests.data.length === 0 ? (
        <p className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          No purchase requests{status ? ` with status ${REQUEST_LABEL[status].toLowerCase()}` : ""}.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Reason / decision</TableHead>
                <TableHead>Order</TableHead>
                <TableHead>Requested</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {requests.data.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    <Link
                      href={`/products/${r.product.id}`}
                      className="font-medium hover:text-brand hover:underline"
                    >
                      {r.product.name}
                    </Link>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{r.quantity}</TableCell>
                  <TableCell>
                    <RequestStatusBadge status={r.status} />
                  </TableCell>
                  <TableCell className="max-w-72 text-xs text-muted-foreground">
                    {r.reason ?? "—"}
                    {r.decision_note ? <span className="block">↳ {r.decision_note}</span> : null}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {r.order_id ? (
                      <Link
                        href={`/purchases/orders/${r.order_id}`}
                        className="text-brand hover:underline"
                      >
                        {r.order_number}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                    {formatRelative(r.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      {r.status === "requested" ? (
                        <>
                          <DecisionDialog
                            request={r}
                            decision="approved"
                            trigger={<Button size="xs">Approve</Button>}
                          />
                          <DecisionDialog
                            request={r}
                            decision="rejected"
                            trigger={
                              <Button size="xs" variant="outline">
                                Reject
                              </Button>
                            }
                          />
                        </>
                      ) : null}
                      {r.status === "approved" ? (
                        <DecisionDialog
                          request={r}
                          decision="cancelled"
                          trigger={
                            <Button size="xs" variant="ghost">
                              Cancel
                            </Button>
                          }
                        />
                      ) : null}
                      {r.status === "rejected" || r.status === "cancelled" ? (
                        <DecisionDialog
                          request={r}
                          decision="requested"
                          trigger={
                            <Button size="xs" variant="ghost">
                              Reopen
                            </Button>
                          }
                        />
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function OrdersTab() {
  const [status, setStatus] = useState<PurchaseOrderStatus | undefined>(undefined);
  const [page, setPage] = useState(1);
  const orders = useQuery({
    ...procurementQueries.orders({ status, page, page_size: 25 }),
    placeholderData: keepPreviousData,
  });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={status ?? ALL}
          onValueChange={(v) => {
            setStatus(v === ALL ? undefined : (v as PurchaseOrderStatus));
            setPage(1);
          }}
        >
          <SelectTrigger className="w-48" aria-label="Order status filter">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All orders</SelectItem>
            {PURCHASE_ORDER_STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {ORDER_LABEL[s]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="ml-auto">
          <NewOrderDialog
            trigger={
              <Button size="sm">
                <Plus /> New order
              </Button>
            }
          />
        </div>
      </div>
      {orders.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : orders.isError ? (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertTitle>Could not load orders</AlertTitle>
          <AlertDescription>{orders.error.message}</AlertDescription>
        </Alert>
      ) : orders.data.items.length === 0 ? (
        <p className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
          No purchase orders yet.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-xl border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Order</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Supplier</TableHead>
                  <TableHead className="text-right">Lines</TableHead>
                  <TableHead className="text-right">Received</TableHead>
                  <TableHead className="text-right">Total</TableHead>
                  <TableHead>Expected</TableHead>
                  <TableHead>Tracking</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {orders.data.items.map((o) => (
                  <TableRow key={o.id}>
                    <TableCell>
                      <Link
                        href={`/purchases/orders/${o.id}`}
                        className="font-mono text-sm font-medium hover:text-brand hover:underline"
                      >
                        {o.order_number}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <OrderStatusBadge status={o.status} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {o.supplier_name ?? "—"}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">{o.item_count}</TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {o.units_received}/{o.units_ordered}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">
                      {formatMoney(o.total_cost, o.currency)}
                    </TableCell>
                    <TableCell className="text-xs whitespace-nowrap text-muted-foreground">
                      {o.expected_delivery_at ? formatDateTime(o.expected_delivery_at) : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {o.tracking_url ? (
                        <a
                          href={o.tracking_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-brand hover:underline"
                        >
                          {o.tracking_number ?? "track"} <ExternalLink className="size-3" />
                        </a>
                      ) : (
                        (o.tracking_number ?? "—")
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <MediaPagination
            page={orders.data.page}
            totalPages={orders.data.total_pages}
            total={orders.data.total}
            pageSize={orders.data.page_size}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  );
}

export function PurchasesPage() {
  const stats = useQuery(procurementQueries.stats());
  return (
    <div className="space-y-6">
      <PageHeader
        title="Purchases"
        description="Approve what gets bought for filming, track orders and deliveries, and record what actually arrived. Procurement never changes a product's research status."
        actions={
          stats.data ? (
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary" className="font-mono">
                {stats.data.requests_by_status.requested} awaiting approval
              </Badge>
              <Badge variant="outline" className="font-mono">
                {stats.data.requests_by_status.approved} approved
              </Badge>
              <Badge variant="outline" className="font-mono">
                {stats.data.open_orders} open orders
              </Badge>
              <Badge variant="outline" className="font-mono">
                {stats.data.units_received}/{stats.data.units_ordered} units received
              </Badge>
            </div>
          ) : null
        }
      />
      <Tabs defaultValue="requests">
        <TabsList>
          <TabsTrigger value="requests">Requests</TabsTrigger>
          <TabsTrigger value="orders">Orders</TabsTrigger>
        </TabsList>
        <TabsContent value="requests" className="pt-4">
          <RequestsTab />
        </TabsContent>
        <TabsContent value="orders" className="pt-4">
          <OrdersTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
