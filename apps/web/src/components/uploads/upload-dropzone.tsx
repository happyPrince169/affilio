"use client";

import { FolderInput, Upload } from "lucide-react";
import { type DragEvent, useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface UploadDropzoneProps {
  allowedExtensions: string[];
  disabled?: boolean;
  disabledReason?: string;
  onFiles: (files: File[]) => void;
}

/** Folder entries from a drop (Chromium, Firefox, Safari via webkitGetAsEntry). */
async function filesFromDataTransfer(transfer: DataTransfer): Promise<File[]> {
  const items = Array.from(transfer.items ?? []);
  const entries = items
    .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
    .filter((e): e is FileSystemEntry => e !== null);
  if (entries.length === 0) return Array.from(transfer.files ?? []);
  const out: File[] = [];
  const walk = async (entry: FileSystemEntry): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      );
      out.push(file);
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      let batch: FileSystemEntry[];
      do {
        batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
          reader.readEntries(resolve, reject),
        );
        for (const child of batch) await walk(child);
      } while (batch.length > 0);
    }
  };
  for (const entry of entries) await walk(entry);
  return out;
}

export function UploadDropzone({
  allowedExtensions,
  disabled,
  disabledReason,
  onFiles,
}: UploadDropzoneProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [folderSupported] = useState(
    () => typeof document !== "undefined" && "webkitdirectory" in document.createElement("input"),
  );

  const onDrop = useCallback(
    async (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setDragging(false);
      if (disabled) return;
      const files = await filesFromDataTransfer(event.dataTransfer);
      if (files.length) onFiles(files);
    },
    [disabled, onFiles],
  );

  const accept = allowedExtensions.join(",");

  return (
    <div
      role="region"
      aria-label="Add videos"
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-6 text-center transition-colors sm:p-10",
        dragging ? "border-primary bg-primary/5" : "border-muted-foreground/30",
        disabled && "opacity-60",
      )}
    >
      <Upload className="size-8 text-muted-foreground" aria-hidden="true" />
      <div>
        <p className="font-medium">Drag and drop finished videos here</p>
        <p className="text-sm text-muted-foreground">
          {disabled
            ? (disabledReason ?? "Choose a production owner and categories first")
            : `${allowedExtensions.join(", ")} · many files at once · uploads resume if the connection drops`}
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        <Button type="button" onClick={() => fileInput.current?.click()} disabled={disabled}>
          <Upload /> Select files
        </Button>
        {folderSupported ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => folderInput.current?.click()}
            disabled={disabled}
          >
            <FolderInput /> Select folder
          </Button>
        ) : null}
      </div>
      <input
        ref={fileInput}
        type="file"
        multiple
        accept={accept}
        className="sr-only"
        aria-label="Select video files"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
      <input
        ref={folderInput}
        type="file"
        multiple
        className="sr-only"
        aria-label="Select a folder of videos"
        // @ts-expect-error non-standard attribute supported by Chromium, Firefox and Safari
        webkitdirectory=""
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}
