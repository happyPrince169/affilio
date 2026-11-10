# ADR 0014 — Field ownership and identity safety for MeoScout ingest

- **Status:** Proposed (M3.2-B.2 P1.1, 2026-11-10); implemented locally, not deployed.
  Amends ADR 0013 §ownership.
- **Context:** P1 (`POST /products/ingest`, ADR 0013) overwrote research fields (`name`,
  `seller_name`, `category`, `images`, prices, commission estimates, `research_notes`)
  whenever MeoScout sent a non-empty value that differed. AFFILIO has no per-field edit
  tracking, so an editor's renamed product, curated images or written notes would be replaced
  by the next batch. The cross-repository reconciliation (P1.1) also found that ingest did not
  check that the URL and the platform id name the same Shopee product, and that the same
  product could enter the catalog twice under two URL spellings.
- **Decision:**
  1. **Ingest owns only what it wrote.** `products.ingest_snapshot` (JSONB) records, per
     field, the value ingest last wrote. For each research field in a batch:
     - incoming empty → nothing (never erase);
     - stored empty → fill, record it;
     - stored equals incoming → nothing;
     - stored still equals the recorded value → refresh, record it;
     - otherwise (an editor, the CSV import or manual entry set it; or ingest never wrote
       it) → **kept**, listed in the item's `preserved_fields`. Not a conflict: the
       candidate is in AFFILIO, and AFFILIO chose its own value.
     Products ingest did not create start with no record, so every stored value is
     AFFILIO's (fill-only). A field returns to ingest's ownership only if it is emptied.
  2. **Unchanged rules.** `affiliate_url` stays a kept conflict unless `on_conflict=replace`
     (MeoScout sends `keep`). Identity (`platform_product_id`, `product_url`) is enrich-only.
     Status, status history, procurement, production, media links, affiliate links,
     channels and publishing are never written by ingest.
  3. **Shopee identity cross-check.** `platform_product_id` must be `<shopId>_<itemId>`;
     `product_url` must be on shopee.vn; ids the URL states (`-i.<shop>.<item>`,
     `/product/<shop>/<item>`, `itemid`/`shopid` query) must equal the id sent. A mismatch is
     `rejected` (`identity_mismatch`), nothing written. A URL that states both ids supplies
     the id when none was sent (read from the URL, never guessed), so the partial unique index
     on `(organization, platform, platform_product_id)` catches the duplicate.
  4. **No merges on partial identity.** When only the item id agrees (a stored bare item id,
     a stored URL with only `itemid`, or a candidate URL with only `itemid` against a stored
     `<shop>_<item>`), ingest returns `conflict` / `identity_ambiguous` with the AFFILIO
     product id and writes nothing. A stored product known only by a URL that states the same
     shop and item ids is the same product and is updated (id enriched).
  5. **Contract pinned to 2.0.x.** Item fields are `extra="forbid"`, so an additive minor
     version would reject items one by one; a newer minor is now a batch-level 422.
  6. **Commission rate** with more than 4 decimals is rounded half-up to the column's
     precision (as the CSV path already does) instead of rejecting the item; the unrounded
     percent stays in the item's `evidence`.
- **Consequences:** An editor's change is never undone by a batch, and the response says
  which fields were kept, so MeoScout can show "AFFILIO kept its own name". The cost: a field
  an editor touched no longer follows MeoScout (e.g. an edited price stops refreshing) until
  someone clears it. Products created before migration 0020 are fill-only. Partial-identity
  conflicts need an operator decision instead of an automatic merge.
- **Alternatives rejected:** per-field "edited by a person" flags set by every write path
  (touches the product service, CSV import and future editors; the snapshot needs no change
  outside ingest); fill-only for everything (stale prices on products only MeoScout ever
  touched); merging on item id alone (assumes Shopee item ids are unique across shops, which
  AFFILIO has not confirmed).
