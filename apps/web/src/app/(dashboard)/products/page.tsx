import type { Metadata } from "next";

import { PlaceholderPage } from "@/components/placeholder-page";

export const metadata: Metadata = { title: "Products" };

export default function ProductsPage() {
  return (
    <PlaceholderPage
      title="Products"
      description="Affiliate product catalog and research workspace for the active Shopee account."
      milestone="M2"
      milestoneTitle="Product Catalog"
      plannedCapabilities={[
        "Manually curated product records with affiliate links and commission notes.",
        "Link products to media assets and planned content.",
        "Basic product status lifecycle (researching, approved, retired).",
        "Import/export via CSV for bulk editing.",
      ]}
      dependencies={["M0 Foundation accepted", "M1 Media Library (for asset linking)"]}
    />
  );
}
