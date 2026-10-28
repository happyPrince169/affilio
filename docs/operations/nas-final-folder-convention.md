# NAS final-folder convention (recommended, not enforced)

## Standard layout (facet classification, ADR 0011)

```
<finals root>/                                   e.g. /volume1/ContentFactory/fashion/FINAL
├── Hang/                                        → Production owner / Hang
│   ├── Mens-Fashion/                            → Product category / Men's Fashion
│   │   ├── Review/        video.mp4             → Content type / Review
│   │   ├── Try-On/
│   │   ├── Couple/
│   │   ├── Lifestyle/
│   │   └── Comparison/
│   ├── Womens-Fashion/ …
│   ├── Accessories/ …
│   └── Sports/ …
├── Hao/ …                                       same tree
├── Minh Anh/ …                                  same tree
└── Hang/T9/, Hao/T9/, Minh Anh/T9/              legacy period folders: left as they are
```

- Folder names match categories by normalized name or slug: `Mens-Fashion` is "Men's Fashion".
- A new folder at the product-category or content-type level (or a new owner) is **not** a
  category until an admin approves it under Settings → Folder mapping → "New folders found by
  the scanner". Until then its videos are classified by what is known (owner) and fall back to
  Uncategorized. Empty folders are ignored.
- The production period is the month AFFILIO first indexed the video, not a folder name.
- Create the tree with `python3 apps/api/src/affilio/scripts/media_folder_bootstrap.py --root
  <finals root>` (dry run), then `--apply`. It only creates missing directories.

## Legacy single-category layout (M3A.1 folder rules)

AFFILIO classifies finished videos by the folder they are uploaded into under the finals
root (`MEDIA_FINALS_PATH`, mounted read-only). The physical root is configuration; the
folder names below are a **recommendation** that must be mirrored by folder rules in
Settings → Folder mapping. AFFILIO never creates, renames, moves or deletes anything there.

```
<finals root>/                       e.g. /volume1/ContentFactory/finals (illustrative)
├── mens-sports/                     → Men's Sports
│   ├── football/                    → Men's Sports / Football
│   ├── pickleball/                  → Men's Sports / Pickleball
│   └── running/                     → Men's Sports / Running
├── mens-fashion/                    → Men's Fashion
├── womens-fashion/                  → Women's Fashion
├── home-living/                     → Home & Living
│   ├── kitchen/                     → Home & Living / Kitchen
│   └── cleaning/                    → Home & Living / Cleaning
├── mom-baby/                        → Mom & Baby
└── uncategorized/                   → Uncategorized (same as any unmapped folder)
```

Rules of thumb for editors:

1. Upload the finished file directly into the deepest matching folder. Sub-folders you
   create for your own organisation (dates, campaigns) inherit the parent mapping as long as
   the rule has "include subfolders" on.
2. Finish the upload before the settle window (`MEDIA_FINAL_SETTLE_SECONDS`) elapses; files
   still being written are skipped until they are stable.
3. Moving a file to another category folder reclassifies it on the next scan. Its AFFILIO
   identity, handoff decision and manual category overrides are kept. Approved publications
   that reference it get a "video category changed" flag for review.
4. Do not rename files to change categories: use folders. Do not create per-Page folders:
   one file serves every Page it is routed to.
5. Anything in an unmapped folder lands in Uncategorized and shows up in the "Folders on the
   NAS" report so an admin can add a rule.

Admin checklist when adding a folder: create the category (Settings → Categories), add the
rule (Settings → Folder mapping, use the preview), trigger a finals scan, confirm the videos
show the badge in the Content Library.
