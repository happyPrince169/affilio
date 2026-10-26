import type { Metadata } from "next";
import { Suspense } from "react";

import { ContentLibrary } from "@/components/media/content-library";
import { MediaGridSkeleton } from "@/components/media/media-grid";

export const metadata: Metadata = { title: "Content Library" };

export default function ContentLibraryPage() {
  // useSearchParams() inside ContentLibrary requires a Suspense boundary for prerendering.
  return (
    <Suspense fallback={<MediaGridSkeleton />}>
      <ContentLibrary />
    </Suspense>
  );
}
