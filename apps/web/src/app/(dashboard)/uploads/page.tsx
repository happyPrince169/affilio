import type { Metadata } from "next";

import { UploadVideos } from "@/components/uploads/upload-videos";

export const metadata: Metadata = { title: "Uploads" };

export default function UploadsPage() {
  return <UploadVideos />;
}
