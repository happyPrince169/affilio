"use client";

import type { ImportPreview, ProductImport } from "@affilio/shared-types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, LoaderCircle, Upload } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { previewImport, productQueries, runImport } from "@/lib/api/products";

const NONE = "__none__";
const FIELD_LABEL: Record<string, string> = {
  name: "Product name *",
  product_url: "Product URL",
  affiliate_url: "Affiliate URL",
  platform_product_id: "Platform product ID",
  seller_name: "Seller / shop",
  category: "Category",
  image_url: "Image URL",
  observed_price: "Price",
  currency: "Currency",
  estimated_commission_rate: "Commission (% or fraction)",
  estimated_commission_amount: "Commission amount",
  source_ref: "MeoScout ID",
  research_notes: "Notes",
};

const ACTION_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  create: "default",
  update: "secondary",
  skip: "outline",
  error: "destructive",
};

export function ImportDialog({ trigger }: { trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [onDuplicate, setOnDuplicate] = useState<"skip" | "update">("skip");
  const [force, setForce] = useState(false);
  const [result, setResult] = useState<ProductImport | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const reset = () => {
    setFile(null);
    setPreview(null);
    setMapping({});
    setForce(false);
    setResult(null);
  };

  const previewMutation = useMutation({
    mutationFn: (args: { file: File; mapping?: Record<string, string> }) =>
      previewImport(args.file, args.mapping, onDuplicate),
    onSuccess: (data) => {
      setPreview(data);
      setMapping(data.mapping);
    },
    onError: (error) => toast.error("Could not read file", { description: error.message }),
  });

  const importMutation = useMutation({
    mutationFn: () => runImport(file!, mapping, onDuplicate, force),
    onSuccess: (data) => {
      setResult(data);
      toast.success(
        data.status === "skipped"
          ? "File already imported"
          : `Imported ${data.created_count} new, ${data.updated_count} updated`,
      );
      void queryClient.invalidateQueries({ queryKey: productQueries.all() });
    },
    onError: (error) => toast.error("Import failed", { description: error.message }),
  });

  const onFile = (picked: File | null) => {
    reset();
    if (!picked) return;
    setFile(picked);
    previewMutation.mutate({ file: picked });
  };

  const changeMapping = (field: string, header: string) => {
    const next = { ...mapping };
    if (header === NONE) delete next[field];
    else next[field] = header;
    setMapping(next);
    if (file) previewMutation.mutate({ file, mapping: next });
  };

  const importable = preview ? preview.counts.create + preview.counts.update : 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Import from MeoScout</DialogTitle>
          <DialogDescription>
            Upload a CSV export. Map its columns, review what will happen to every row, then import.
            Rows already in the catalog (same platform id or URL) are skipped or updated, never
            duplicated. The same file is never imported twice.
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3">
            <Alert variant={result.status === "completed" ? "default" : "destructive"}>
              <FileSpreadsheet />
              <AlertTitle>
                {result.status === "completed" ? "Import finished" : "Nothing imported"}
              </AlertTitle>
              <AlertDescription>
                {result.file_name}: {result.created_count} created · {result.updated_count} updated
                · {result.skipped_count} skipped · {result.failed_count} failed
                {result.duplicate_of_import_id ? " — this exact file was already imported." : ""}
              </AlertDescription>
            </Alert>
            {result.errors.length > 0 ? (
              <ul className="max-h-48 space-y-1 overflow-auto rounded-md bg-muted p-2 font-mono text-[11px]">
                {result.errors.map((e, i) => (
                  <li key={i}>
                    row {e.row} {e.name ? `(${e.name})` : ""}: {e.message}
                  </li>
                ))}
              </ul>
            ) : null}
            <DialogFooter>
              <Button onClick={() => setOpen(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <input
                ref={inputRef}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(event) => onFile(event.target.files?.[0] ?? null)}
              />
              <Button variant="outline" onClick={() => inputRef.current?.click()}>
                <Upload /> {file ? "Choose another file" : "Choose CSV file"}
              </Button>
              {file ? (
                <span className="text-sm text-muted-foreground">
                  {file.name} · {(file.size / 1024).toFixed(1)} KB
                </span>
              ) : null}
              {previewMutation.isPending ? (
                <LoaderCircle className="size-4 animate-spin text-muted-foreground" />
              ) : null}
              <div className="ml-auto flex items-center gap-2 text-sm">
                <Label htmlFor="on-duplicate">Existing products</Label>
                <Select
                  value={onDuplicate}
                  onValueChange={(v) => {
                    setOnDuplicate(v as "skip" | "update");
                    if (file) previewMutation.mutate({ file, mapping });
                  }}
                >
                  <SelectTrigger id="on-duplicate" className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="skip">Skip</SelectItem>
                    <SelectItem value="update">Update</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {preview ? (
              <>
                <div>
                  <p className="mb-2 text-sm font-medium">Column mapping</p>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {preview.target_fields.map((field) => (
                      <div key={field} className="flex items-center justify-between gap-2 text-sm">
                        <span className="text-muted-foreground">{FIELD_LABEL[field] ?? field}</span>
                        <Select
                          value={mapping[field] ?? NONE}
                          onValueChange={(header) => changeMapping(field, header)}
                        >
                          <SelectTrigger className="w-44" aria-label={`Column for ${field}`}>
                            <SelectValue placeholder="—" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={NONE}>— not imported —</SelectItem>
                            {preview.headers.map((header) => (
                              <SelectItem key={header} value={header}>
                                {header}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary" className="font-mono">
                    {preview.row_count} rows
                  </Badge>
                  <Badge className="font-mono">{preview.counts.create} new</Badge>
                  <Badge variant="secondary" className="font-mono">
                    {preview.counts.update} update
                  </Badge>
                  <Badge variant="outline" className="font-mono">
                    {preview.counts.skip} skip
                  </Badge>
                  <Badge variant="destructive" className="font-mono">
                    {preview.counts.error} invalid
                  </Badge>
                </div>

                {preview.already_imported ? (
                  <Alert>
                    <FileSpreadsheet />
                    <AlertTitle>This exact file was already imported</AlertTitle>
                    <AlertDescription>
                      <label className="mt-1 flex items-center gap-2 text-sm">
                        <Checkbox checked={force} onCheckedChange={(v) => setForce(v === true)} />
                        Import anyway (duplicates are still skipped or updated, never created twice)
                      </label>
                    </AlertDescription>
                  </Alert>
                ) : null}

                <div className="max-h-64 overflow-auto rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-16">Row</TableHead>
                        <TableHead className="w-24">Action</TableHead>
                        <TableHead>Name</TableHead>
                        <TableHead>Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {preview.rows.map((row) => (
                        <TableRow key={row.row_number}>
                          <TableCell className="font-mono text-xs">{row.row_number}</TableCell>
                          <TableCell>
                            <Badge variant={ACTION_VARIANT[row.action]}>{row.action}</Badge>
                          </TableCell>
                          <TableCell className="truncate">{row.name ?? "—"}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {row.reason ?? ""}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {preview.row_count > preview.rows.length ? (
                    <p className="p-2 text-center text-xs text-muted-foreground">
                      Showing the first {preview.rows.length} of {preview.row_count} rows.
                    </p>
                  ) : null}
                </div>

                <DialogFooter>
                  <Button variant="outline" onClick={() => setOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    disabled={
                      importable === 0 ||
                      importMutation.isPending ||
                      previewMutation.isPending ||
                      (preview.already_imported && !force)
                    }
                    onClick={() => importMutation.mutate()}
                  >
                    {importMutation.isPending ? <LoaderCircle className="animate-spin" /> : null}
                    Import {importable} row{importable === 1 ? "" : "s"}
                  </Button>
                </DialogFooter>
              </>
            ) : (
              <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                Choose a MeoScout CSV export to see a preview. Nothing is written until you confirm.
              </p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
