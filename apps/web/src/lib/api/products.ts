import type {
  ImportPreview,
  LinkedProduct,
  ProductImport,
  PaginatedProducts,
  ProductCreate,
  ProductDetail,
  ProductFilters,
  ProductMediaLink,
  ProductMediaLinkCreate,
  ProductMediaLinkUpdate,
  ProductStats,
  ProductStatusChange,
  ProductStatusEvent,
  ProductUpdate,
} from "@affilio/shared-types";
import { queryOptions } from "@tanstack/react-query";

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

const PRODUCTS = `${API_V1_PREFIX}/products`;
const JSON_HEADERS = { "Content-Type": "application/json" };

function toSearchParams(filters: ProductFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const fetchProducts = (filters: ProductFilters) =>
  apiFetch<PaginatedProducts>(`${PRODUCTS}${toSearchParams(filters)}`);
export const fetchProduct = (id: string) =>
  apiFetch<ProductDetail>(`${PRODUCTS}/${encodeURIComponent(id)}`);
export const fetchProductStats = () => apiFetch<ProductStats>(`${PRODUCTS}/stats`);
export const fetchProductMedia = (id: string) =>
  apiFetch<ProductMediaLink[]>(`${PRODUCTS}/${encodeURIComponent(id)}/media`);
export const fetchProductsForMedia = (assetId: string) =>
  apiFetch<LinkedProduct[]>(`${PRODUCTS}/media/${encodeURIComponent(assetId)}`);
export const fetchStatusHistory = (id: string) =>
  apiFetch<ProductStatusEvent[]>(`${PRODUCTS}/${encodeURIComponent(id)}/status-history`);

export const createProduct = (body: ProductCreate) =>
  apiFetch<ProductDetail>(PRODUCTS, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
export const updateProduct = (id: string, body: ProductUpdate) =>
  apiFetch<ProductDetail>(`${PRODUCTS}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
export const changeProductStatus = (id: string, body: ProductStatusChange) =>
  apiFetch<ProductDetail>(`${PRODUCTS}/${encodeURIComponent(id)}/status`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
export const linkProductMedia = (id: string, body: ProductMediaLinkCreate) =>
  apiFetch<ProductMediaLink>(`${PRODUCTS}/${encodeURIComponent(id)}/media`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  });
export const updateProductMediaLink = (id: string, assetId: string, body: ProductMediaLinkUpdate) =>
  apiFetch<ProductMediaLink>(
    `${PRODUCTS}/${encodeURIComponent(id)}/media/${encodeURIComponent(assetId)}`,
    { method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(body) },
  );
export const unlinkProductMedia = (id: string, assetId: string) =>
  apiFetch<void>(`${PRODUCTS}/${encodeURIComponent(id)}/media/${encodeURIComponent(assetId)}`, {
    method: "DELETE",
  });

/** Multipart upload helpers for the MeoScout CSV import. */
function importForm(
  file: File,
  mapping?: Record<string, string>,
  onDuplicate = "skip",
  force = false,
) {
  const form = new FormData();
  form.append("file", file, file.name);
  if (mapping) form.append("mapping", JSON.stringify(mapping));
  form.append("on_duplicate", onDuplicate);
  if (force) form.append("force", "true");
  return form;
}

export const previewImport = (file: File, mapping?: Record<string, string>, onDuplicate = "skip") =>
  apiFetch<ImportPreview>(`${PRODUCTS}/imports/preview`, {
    method: "POST",
    body: importForm(file, mapping, onDuplicate),
  });
export const runImport = (
  file: File,
  mapping: Record<string, string>,
  onDuplicate = "skip",
  force = false,
) =>
  apiFetch<ProductImport>(`${PRODUCTS}/imports`, {
    method: "POST",
    body: importForm(file, mapping, onDuplicate, force),
  });
export const fetchImports = (limit = 10) =>
  apiFetch<ProductImport[]>(`${PRODUCTS}/imports?limit=${limit}`);

export const productQueries = {
  imports: (limit = 10) =>
    queryOptions({
      queryKey: [...productQueries.all(), "imports", limit] as const,
      queryFn: () => fetchImports(limit),
      staleTime: 5_000,
    }),
  all: () => ["products"] as const,
  list: (filters: ProductFilters) =>
    queryOptions({
      queryKey: [...productQueries.all(), "list", filters] as const,
      queryFn: () => fetchProducts(filters),
      staleTime: 5_000,
    }),
  detail: (id: string) =>
    queryOptions({
      queryKey: [...productQueries.all(), "detail", id] as const,
      queryFn: () => fetchProduct(id),
      staleTime: 5_000,
    }),
  stats: () =>
    queryOptions({
      queryKey: [...productQueries.all(), "stats"] as const,
      queryFn: fetchProductStats,
      staleTime: 5_000,
    }),
  media: (id: string) =>
    queryOptions({
      queryKey: [...productQueries.all(), "media", id] as const,
      queryFn: () => fetchProductMedia(id),
      staleTime: 5_000,
    }),
  forAsset: (assetId: string) =>
    queryOptions({
      queryKey: [...productQueries.all(), "for-asset", assetId] as const,
      queryFn: () => fetchProductsForMedia(assetId),
      staleTime: 5_000,
    }),
};
