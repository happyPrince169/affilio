"use client";

import { useQuery } from "@tanstack/react-query";
import { TriangleAlert, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { authQueries } from "@/lib/api/auth";
import { uploadQueries } from "@/lib/api/uploads";
import { isBatchFinished, summarize } from "@/lib/uploads/queue";

import { BatchSettings } from "./batch-settings";
import { MyUploads } from "./my-uploads";
import { UploadDropzone } from "./upload-dropzone";
import { UploadQueue } from "./upload-queue";
import { useUploadQueue } from "./use-upload-queue";

function subscribeOnline(callback: () => void) {
  window.addEventListener("online", callback);
  window.addEventListener("offline", callback);
  return () => {
    window.removeEventListener("online", callback);
    window.removeEventListener("offline", callback);
  };
}

function useOnline(): boolean {
  return useSyncExternalStore(
    subscribeOnline,
    () => navigator.onLine,
    () => true,
  );
}

export function UploadVideos() {
  const { can } = useAuth();
  const canUpload = can("edit");
  const online = useOnline();
  const options = useQuery({ ...uploadQueries.options(), enabled: canUpload });
  const members = useQuery({
    ...authQueries.members(),
    enabled: options.data?.can_submit_for_others === true,
  });
  const queue = useUploadQueue(options.data);
  const summary = useMemo(() => summarize(queue.items), [queue.items]);
  const [tab, setTab] = useState("upload");

  const defaultsReady = Boolean(
    queue.defaults.ownerId && queue.defaults.productCategoryId && queue.defaults.contentTypeId,
  );
  const finished = isBatchFinished(queue.items);

  // Warn before leaving while uploads are in flight (bytes already sent are kept server-side).
  useEffect(() => {
    const active = queue.items.some((i) => ["creating", "uploading"].includes(i.state));
    if (!active) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [queue.items]);

  const onFiles = (files: File[]) => {
    const result = queue.addFiles(files);
    if (result.skipped)
      toast.warning(`${result.skipped} file(s) skipped: not a supported video type`);
    if (result.resumed) toast.success(`${result.resumed} interrupted upload(s) will continue`);
    if (result.mismatched)
      toast.error(`${result.mismatched} file(s) did not match the interrupted upload`);
  };

  if (!canUpload) {
    return (
      <>
        <PageHeader title="Uploads" description="Submit finished videos for review." />
        <Alert>
          <TriangleAlert />
          <AlertTitle>Your role cannot upload videos</AlertTitle>
          <AlertDescription>
            Ask an admin for the editor role and a production owner.
          </AlertDescription>
        </Alert>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Uploads"
        description="Submit finished videos. Each file uploads on its own, resumes after an interruption and goes to review once the server has filed it."
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="upload">Upload videos</TabsTrigger>
          <TabsTrigger value="mine">My uploads</TabsTrigger>
        </TabsList>
        <TabsContent value="upload" className="space-y-4 pt-4">
          {!online ? (
            <Alert>
              <WifiOff />
              <AlertTitle>You are offline</AlertTitle>
              <AlertDescription>
                Uploads pause automatically and continue from the last confirmed byte when the
                connection is back.
              </AlertDescription>
            </Alert>
          ) : null}
          {options.isPending ? (
            <p className="text-sm text-muted-foreground">Loading upload settings…</p>
          ) : options.isError ? (
            <Alert variant="destructive">
              <TriangleAlert />
              <AlertTitle>Uploads unavailable</AlertTitle>
              <AlertDescription>{options.error.message}</AlertDescription>
            </Alert>
          ) : !options.data.facets_enabled ? (
            <Alert>
              <TriangleAlert />
              <AlertTitle>Categories are not set up yet</AlertTitle>
              <AlertDescription>
                An admin must enable facets and create production owners, product categories and
                content types under Settings → Categories.
              </AlertDescription>
            </Alert>
          ) : options.data.owners.length === 0 ? (
            <Alert>
              <TriangleAlert />
              <AlertTitle>No production owner assigned to you</AlertTitle>
              <AlertDescription>
                Ask an admin to map your account to a production owner under Settings → Members.
              </AlertDescription>
            </Alert>
          ) : (
            <>
              {queue.correction ? (
                <Alert>
                  <AlertTitle>
                    Submitting a corrected version of “{queue.correction.title}”
                  </AlertTitle>
                  <AlertDescription className="flex flex-wrap items-center gap-2">
                    The next file you add replaces the rejected video and keeps its categories.
                    <Button variant="ghost" size="sm" onClick={queue.clearCorrection}>
                      Cancel
                    </Button>
                  </AlertDescription>
                </Alert>
              ) : null}
              {queue.needsFileCount > 0 ? (
                <Alert>
                  <AlertTitle>
                    {queue.needsFileCount} upload(s) from an earlier session can continue
                  </AlertTitle>
                  <AlertDescription>
                    Browsers cannot keep files across a reload. Select the same files again (or drop
                    the folder); each is checked against the bytes already uploaded before it
                    continues. Cancel the ones you no longer want.
                  </AlertDescription>
                </Alert>
              ) : null}
              <BatchSettings
                options={options.data}
                defaults={queue.defaults}
                onChange={queue.setDefaults}
                concurrency={queue.concurrency}
                onConcurrency={queue.setConcurrency}
                members={members.data}
              />
              <UploadDropzone
                allowedExtensions={options.data.allowed_extensions}
                disabled={!defaultsReady && !queue.correction && queue.needsFileCount === 0}
                onFiles={onFiles}
              />
              <UploadQueue
                items={queue.items}
                defaults={queue.defaults}
                options={options.data}
                summary={summary}
                onOverride={queue.setOverride}
                onPause={queue.pause}
                onResume={queue.resume}
                onCancel={queue.cancel}
                onRetry={queue.retry}
                onRemove={queue.remove}
                onClearFinished={queue.clearFinished}
              />
              {finished ? (
                <p className="text-sm text-muted-foreground" aria-live="polite">
                  Batch finished. Completed videos are in the library awaiting review; see “My
                  uploads” for reviewer feedback.
                </p>
              ) : null}
            </>
          )}
        </TabsContent>
        <TabsContent value="mine" className="pt-4">
          <MyUploads
            onCorrect={(ctx) => {
              queue.startCorrection(ctx);
              queue.setDefaults({
                ownerId: ctx.ownerId,
                productCategoryId: ctx.productCategoryId,
                contentTypeId: ctx.contentTypeId,
              });
              setTab("upload");
            }}
          />
        </TabsContent>
      </Tabs>
    </>
  );
}
