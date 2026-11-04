# Browser acceptance checklist — editor uploads and handoff (W0)

Covers the daily production workflow in a real browser: an editor delivers finished videos,
an admin reviews them, the production report attributes the work. API-level behaviour is
already verified in production (Phase D smoke test, 2026-11-04); this list is what only a
browser and a second account can prove. Run it once against production after the W1 release
and again after any change to `components/uploads`, `components/media` or the handoff review.

## Accounts needed

| Account | Role | How |
| --- | --- | --- |
| Admin | admin or owner | exists (`mia.nguyen.vu@gmail.com` in production) |
| Editor | editor, mapped to one production owner | create under Settings → Members (email, temporary password, role editor), then in the same row add a production owner with "Add owner…" |

The editor must change the temporary password after first sign-in (Settings → Account).
Mapping is required: without it the Uploads page shows "No production owner assigned to you"
and refuses to start.

## Checklist

Each line was executed with headless Chrome against a local stack on 2026-11-04
(`scripts/browser_acceptance_uploads.js`, 31 of 32 checks green after the W1 fixes; the one
remaining line is the dev-server diagnostic listed below); the production run is done by a
person.

| # | Step (as the editor unless stated) | Expected |
| --- | --- | --- |
| 1 | Sign in, open Uploads | "Upload videos" tab; the Production owner dropdown lists only the mapped owner(s) |
| 2 | Choose owner, product category, content type; drop three videos at once (or use "Select video files") | Three rows appear; two upload at a time, the third shows "Queued" until a slot frees; each row shows bytes and a progress bar |
| 3 | Wait | Each row goes Uploading → Processing → Completed with an "Open in library" link; the batch message says the videos await review |
| 4 | Add a fourth video, press Pause on its row | Row shows Paused, progress stops, Resume appears; pressing Resume continues from the same offset to Completed |
| 5 | Add a fifth video, press Cancel while it uploads | Row shows Cancelled; My uploads lists it as Cancelled; no asset in the library |
| 6 | Add a sixth video; while it uploads, reload the page | Banner "upload(s) from an earlier session can continue"; the row shows "Needs file" with the interrupted offset; select the same file again → "interrupted upload(s) will continue" → Completed; selecting a different file is refused with a clear message |
| 7 | Open My uploads | Every completed video listed with owner, category, type, "Pending review"; cancelled and duplicate ones labelled |
| 8 | Open Content Library | The uploads appear with thumbnails (within a worker poll), an "Uploaded" badge, and filter by owner / category / type / source works |
| 9 | Open one upload's asset page | The video preview plays in the page (seek works); path shows `uploads/ready/…`; Source "Uploaded through AFFILIO"; the reclassification card offers only mapped owners |
| 10 | Try to upload for an owner you are not mapped to (another editor's owner) | Not offered in the dropdown; a direct API call answers 403 |
| 11 | Admin: open the same asset, Reject with a note | Dialog requires the note; badge turns Rejected; the note appears on the page |
| 12 | Editor: My uploads | The rejected video shows the reviewer note and "Submit corrected version" |
| 13 | Editor: Submit corrected version, add a new file | The queue shows the correction banner; the new row completes with "Corrected version"; its asset page links to the rejected original |
| 14 | Admin: Approve the corrected video with a note | Badge Approved; export card shows the publishing-readiness state; no publication is created |
| 15 | Admin: Production report | The editor's name, owner, pending / approved / rejected counts, one approved unit for the corrected chain, reviewer notes per video, CSV export downloads |
| 16 | Admin: Settings → Audit log (or `GET /api/v1/auth/audit`) | `upload.created/uploaded/completed`, `upload.duplicate`, `media.facets_reclassified`, `media.handoff_rejected`, `media.handoff_approved` with the admin as actor |
| 17 | Editor: open Production report | "limited to admins" notice, no data |

## Known non-blockers

- The Next.js development server logs "Could not validate that a segment in your UI has
  instant navigation" for `/uploads`; it is a dev-only diagnostic and does not occur in the
  production build.
- Firefox and Safari always need the file reselected after a reload (documented in the
  design); Chromium persistent file handles are not implemented.
