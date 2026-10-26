import type { Metadata } from "next";

import { PlaceholderPage } from "@/components/placeholder-page";

export const metadata: Metadata = { title: "Content Library" };

export default function ContentLibraryPage() {
  return (
    <PlaceholderPage
      title="Content Library"
      description="Centralized library for short-form vertical video and supporting assets."
      milestone="M1"
      milestoneTitle="Media Library"
      plannedCapabilities={[
        "Index media stored on the Synology NAS without copying originals.",
        "Capture technical metadata (duration, resolution, codec) via FFmpeg.",
        "Tag, search, and filter assets per organization.",
        "Track which assets have been used in published content.",
      ]}
      dependencies={[
        "M0 Foundation accepted",
        "Read access to the Synology NAS share from the API host",
        "FFmpeg available on the API host",
      ]}
    />
  );
}
