/** Content taxonomy (M3A.1). Mirrors `apps/api/src/affilio/modules/taxonomy/schemas.py`. */

import type { CategoryStatus } from "./domain";

export interface CategoryRef {
  id: string;
  slug: string;
  name: string;
  path: string;
}

export interface ContentCategory {
  id: string;
  parent_id: string | null;
  slug: string;
  name: string;
  description: string | null;
  status: CategoryStatus;
  sort_order: number;
  depth: number;
  path: string;
  path_slugs: string[];
  children_count: number;
  created_at: string;
  updated_at: string;
}

export interface CategoryCreate {
  name: string;
  slug?: string | null;
  parent_id?: string | null;
  description?: string | null;
  sort_order?: number;
}

export interface CategoryUpdate {
  name?: string;
  slug?: string;
  parent_id?: string | null;
  description?: string | null;
  sort_order?: number;
  status?: CategoryStatus;
}
