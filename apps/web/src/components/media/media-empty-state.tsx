import type { MediaRootStatus, ScanStatus } from "@affilio/shared-types";
import { FolderOpen, HardDrive, SearchX } from "lucide-react";

interface MediaEmptyStateProps {
  scanStatus: ScanStatus | undefined;
  hasFilters: boolean;
  onClearFilters: () => void;
}

function rootByKey(roots: MediaRootStatus[] | undefined, key: string) {
  return roots?.find((root) => root.key === key);
}

export function MediaEmptyState({ scanStatus, hasFilters, onClearFilters }: MediaEmptyStateProps) {
  const originals = rootByKey(scanStatus?.roots, "originals");
  const base =
    "flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center";

  if (hasFilters) {
    return (
      <div className={base}>
        <SearchX className="size-8 text-muted-foreground" />
        <h2 className="text-lg font-semibold">No media matches these filters</h2>
        <p className="max-w-md text-sm text-muted-foreground">
          Try a different search term or clear the filters.
        </p>
        <button
          type="button"
          onClick={onClearFilters}
          className="text-sm font-medium text-brand underline-offset-4 hover:underline"
        >
          Clear filters
        </button>
      </div>
    );
  }

  if (originals && !originals.configured) {
    return (
      <div className={base}>
        <HardDrive className="size-8 text-muted-foreground" />
        <h2 className="text-lg font-semibold">No media root configured</h2>
        <p className="max-w-lg text-sm text-muted-foreground">
          Point <code className="font-mono text-xs">MEDIA_ORIGINALS_PATH</code> at the folder (or
          mounted NAS share) that holds the original videos, restart the API and worker, then run a
          scan. Originals are only ever read, never modified.
        </p>
      </div>
    );
  }

  if (originals && !originals.readable) {
    return (
      <div className={base}>
        <HardDrive className="size-8 text-destructive" />
        <h2 className="text-lg font-semibold">Originals root is not readable</h2>
        <p className="max-w-lg text-sm text-muted-foreground">
          The configured originals directory does not exist or the service user cannot read it.
          Check the mount and file permissions, then try again.
        </p>
      </div>
    );
  }

  return (
    <div className={base}>
      <FolderOpen className="size-8 text-muted-foreground" />
      <h2 className="text-lg font-semibold">The library is empty</h2>
      <p className="max-w-md text-sm text-muted-foreground">
        {scanStatus?.current
          ? "A scan is running. Assets appear here as soon as they are indexed."
          : "Run a scan to index the videos in the originals root. Thumbnails are generated in the background afterwards."}
      </p>
    </div>
  );
}
