import type { Metadata } from "next";

import { ProductionReport } from "@/components/reports/production-report";

export const metadata: Metadata = { title: "Production report" };

export default function ProductionReportPage() {
  return <ProductionReport />;
}
