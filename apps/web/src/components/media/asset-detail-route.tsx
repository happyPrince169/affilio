"use client";

import { useParams } from "next/navigation";

import { AssetDetail } from "@/components/media/asset-detail";

export function AssetDetailRoute() {
  const params = useParams<{ id: string }>();
  return <AssetDetail id={params.id} />;
}
