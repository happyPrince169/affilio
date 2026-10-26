/** Channel routing (M3A.1). Mirrors accounts (channel profile) and routing schemas. */

import type { ChannelMatchPolicy, Platform } from "./domain";
import type { CategoryRef } from "./taxonomy";

export interface ChannelCategoryRuleInput {
  category_id: string;
  match_policy: ChannelMatchPolicy;
  priority?: number;
}

export interface ChannelProfileUpdate {
  primary_category_id?: string | null;
  description?: string | null;
  target_audience?: string | null;
  language?: string | null;
  country?: string | null;
  routing_enabled: boolean;
  rules: ChannelCategoryRuleInput[];
}

export interface ChannelCategoryRule {
  category: CategoryRef;
  match_policy: ChannelMatchPolicy;
  priority: number;
}

export interface ChannelProfile {
  social_account_id: string;
  primary_category: CategoryRef | null;
  description: string | null;
  target_audience: string | null;
  language: string | null;
  country: string | null;
  routing_enabled: boolean;
  rules: ChannelCategoryRule[];
  updated_at: string | null;
}

export interface ChannelRecommendation {
  social_account_id: string;
  display_name: string;
  platform: Platform;
  connection_label: string;
  account_status: string;
  eligible: boolean;
  recommended: boolean;
  /** Routing relevance only; not an engagement or revenue prediction. */
  score: number;
  reasons: string[];
  blocking_reason: string | null;
  matched_categories: CategoryRef[];
  already_targeted: boolean;
}

export interface RecommendationsResponse {
  media_asset_id: string;
  media_title: string;
  media_approved: boolean;
  folder_path: string | null;
  primary_category: CategoryRef | null;
  secondary_categories: CategoryRef[];
  engine_version: string;
  recommend_threshold: number;
  recommended: ChannelRecommendation[];
  low_relevance: ChannelRecommendation[];
  blocked: ChannelRecommendation[];
}
