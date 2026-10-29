import type {
  ProductionPeriod,
  ProductionReportFilters,
  ProductionRowsPage,
  ProductionSummary,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_BASE_URL, API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const PRODUCTION = `${API_V1_PREFIX}/analytics/production`;

export function reportSearch(
  filters: ProductionReportFilters,
  extra: Record<string, string | number | undefined> = {},
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...extra })) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

export const fetchProductionSummary = (filters: ProductionReportFilters) =>
  apiFetch<ProductionSummary>(`${PRODUCTION}/summary${reportSearch(filters)}`);
export const fetchProductionRows = (
  filters: ProductionReportFilters,
  page: number,
  pageSize: number,
) =>
  apiFetch<ProductionRowsPage>(
    `${PRODUCTION}/rows${reportSearch(filters, { page, page_size: pageSize })}`,
  );
export const fetchProductionPeriods = () => apiFetch<ProductionPeriod[]>(`${PRODUCTION}/periods`);
/** Same-origin download link for the CSV export of the current filters. */
export const productionCsvUrl = (filters: ProductionReportFilters) =>
  `${API_BASE_URL}${PRODUCTION}/rows.csv${reportSearch(filters)}`;

export const analyticsQueries = {
  all: () => ["analytics"] as const,
  productionSummary: (filters: ProductionReportFilters) =>
    queryOptions({
      queryKey: [...analyticsQueries.all(), "production", "summary", filters] as const,
      queryFn: () => fetchProductionSummary(filters),
    }),
  productionRows: (filters: ProductionReportFilters, page: number, pageSize: number) =>
    queryOptions({
      queryKey: [...analyticsQueries.all(), "production", "rows", filters, page, pageSize] as const,
      queryFn: () => fetchProductionRows(filters, page, pageSize),
    }),
  productionPeriods: () =>
    queryOptions({
      queryKey: [...analyticsQueries.all(), "production", "periods"] as const,
      queryFn: fetchProductionPeriods,
    }),
};
