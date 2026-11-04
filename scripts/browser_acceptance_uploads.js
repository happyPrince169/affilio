// Browser acceptance run for editor uploads and handoff (W0). See
// docs/operations/browser-acceptance-uploads.md for the human checklist this automates.
//
// Requirements (not project dependencies): a Playwright package reachable through NODE_PATH
// and a Chrome binary (CHROME, default /usr/bin/google-chrome). Runs against a stack where
// the editor/admin accounts below exist (local dev stack; never production credentials).
//
//   NODE_PATH=/path/to/node_modules BASE=http://localhost:3010 CLIPS=./clips OUT=./out \
//     node scripts/browser_acceptance_uploads.js
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://127.0.0.1:3010";
const CLIPS = process.env.CLIPS;
const OUT = process.env.OUT;
const EDITOR = { email: "editor@affilio.local", password: "editor-dev-password-1" };
const ADMIN = { email: "owner@affilio.local", password: "affilio-dev-owner" };
let failed = 0;
const ok = (name, cond, detail = "") => {
  console.log(`${cond ? "ok  " : "FAIL"} ${name}${detail ? ": " + detail : ""}`);
  if (!cond) failed += 1;
  return cond;
};
const clip = (n) => path.join(CLIPS, `w0-clip-${n}.mp4`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function login(page, who) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  // Dev server: wait for hydration before typing, or React never sees the values.
  for (let i = 0; i < 20; i++) {
    await page.fill("#email", who.email);
    await page.fill("#password", who.password);
    await sleep(300);
    if (await page.locator('button[type="submit"]').isEnabled()) break;
  }
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30000 });
}

async function pickOption(page, triggerId, optionText) {
  await page.click(`#${triggerId}`);
  await page.getByRole("option", { name: optionText, exact: true }).click();
}

async function api(ctx, method, url, body) {
  const res = await ctx.request.fetch(`${BASE}${url}`, {
    method,
    data: body,
    headers: body ? { "content-type": "application/json" } : {},
  });
  let json = null;
  try { json = await res.json(); } catch { /* not json */ }
  return { status: res.status(), json };
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: true, args: ["--no-sandbox"] });
  const editorCtx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
  const page = await editorCtx.newPage();
  page.on("pageerror", (e) => console.log("     [pageerror]", e.message));
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });

  // ---- 1. editor login and upload options
  await login(page, EDITOR);
  ok("editor signed in", !page.url().includes("/login"), page.url());
  await page.goto(`${BASE}/uploads`);
  await page.getByText("Upload videos", { exact: true }).first().waitFor({ timeout: 30000 });
  await page.click("#batch-owner");
  const owners = await page.getByRole("option").allInnerTexts();
  await page.keyboard.press("Escape");
  ok("editor sees only the mapped owner (Hang, not Hao)", owners.join(",") === "Hang", owners.join(","));
  await pickOption(page, "batch-owner", "Hang");
  await pickOption(page, "batch-product", "Men's Fashion");
  await pickOption(page, "batch-type", "Review");
  await page.screenshot({ path: `${OUT}/01-upload-settings.png` });

  // ---- 2. bulk add three files, two concurrent sessions
  const input = page.locator('input[aria-label="Select video files"]');
  await input.setInputFiles([clip(1), clip(2), clip(3)]);
  const queue = page.getByRole("list", { name: "Upload queue" });
  await queue.waitFor({ timeout: 15000 });
  let maxConcurrent = 0; let sawQueuedWhileTwoRun = false;
  for (let i = 0; i < 60; i++) {
    const texts = await queue.locator("li").allInnerTexts();
    const uploading = texts.filter((t) => /Uploading|Starting/.test(t)).length;
    const queued = texts.filter((t) => /Queued/.test(t)).length;
    maxConcurrent = Math.max(maxConcurrent, uploading);
    if (uploading === 2 && queued >= 1) sawQueuedWhileTwoRun = true;
    if (texts.every((t) => /Completed|Duplicate|Failed/.test(t)) && texts.length === 3) break;
    await sleep(500);
  }
  ok("at most two sessions upload at once", maxConcurrent <= 2, `max=${maxConcurrent}`);
  ok("third file waited in the queue while two ran", sawQueuedWhileTwoRun);
  await page.screenshot({ path: `${OUT}/02-bulk-progress.png` });
  await page.locator('a[href^="/content/"]').nth(2).waitFor({ timeout: 120000 }).catch(() => {});
  const libLinks = await page.locator('ul[aria-label="Upload queue"] a[href^="/content/"]').count();
  ok("three uploads completed with library links", libLinks === 3, `links=${libLinks}`);
  await page.screenshot({ path: `${OUT}/03-bulk-done.png` });

  // ---- 3. pause / resume
  await input.setInputFiles([clip(4)]);
  await sleep(1500);
  const rows4 = await queue.locator("li", { hasText: "w0-clip-4.mp4" }).count();
  ok("adding one file creates exactly one queue row", rows4 === 1, `rows=${rows4}`);
  const row4 = queue.locator("li", { hasText: "w0-clip-4.mp4" }).first();
  await page.getByRole("button", { name: "Pause w0-clip-4.mp4" }).first().waitFor({ timeout: 20000 });
  await sleep(300);
  await page.getByRole("button", { name: "Pause w0-clip-4.mp4" }).first().click();
  await page.getByRole("button", { name: "Resume w0-clip-4.mp4" }).first().waitFor({ timeout: 10000 });
  const pausedText1 = await row4.innerText(); await sleep(1500); const pausedText2 = await row4.innerText();
  ok("pause stops progress and offers Resume", /Paused/.test(pausedText1) && pausedText1 === pausedText2, pausedText1.replace(/\s+/g, " ").slice(0, 80));
  await page.screenshot({ path: `${OUT}/04-paused.png` });
  await page.getByRole("button", { name: "Resume w0-clip-4.mp4" }).first().click();
  await row4.locator('a[href^="/content/"]').waitFor({ timeout: 120000 }).catch(() => {});
  ok("resumed upload completed", (await row4.locator('a[href^="/content/"]').count()) === 1);

  // ---- 4. cancel
  await input.setInputFiles([clip(5)]);
  const row5 = queue.locator("li", { hasText: "w0-clip-5.mp4" }).first();
  await page.getByRole("button", { name: "Cancel w0-clip-5.mp4" }).first().waitFor({ timeout: 20000 });
  await sleep(300);
  await page.getByRole("button", { name: "Cancel w0-clip-5.mp4" }).first().click();
  await sleep(1500);
  ok("cancelled row shows Cancelled", /Cancelled/.test(await row5.innerText()));
  const mine = await api(editorCtx, "GET", "/api/v1/media/uploads?limit=50");
  const cancelled = (mine.json || []).filter((u) => u.original_filename === "w0-clip-5.mp4");
  ok("server session aborted on cancel", cancelled.length === 1 && cancelled[0].status === "aborted", JSON.stringify(cancelled.map((u) => u.status)));

  // ---- 5. reload recovery with reselection
  await input.setInputFiles([clip(6)]);
  await page.getByRole("button", { name: "Pause w0-clip-6.mp4" }).first().waitFor({ timeout: 20000 });
  await sleep(700); // let a few chunks commit
  await page.reload();
  await page.getByText(/upload\(s\) from an earlier session can continue/).waitFor({ timeout: 30000 });
  await page.screenshot({ path: `${OUT}/05-needs-file.png` });
  const needs = page.getByRole("list", { name: "Upload queue" }).locator("li", { hasText: "w0-clip-6.mp4" }).first();
  ok("interrupted upload listed as needing its file after reload", /needs|Needs|Select the file|reselect/i.test(await needs.innerText()), (await needs.innerText()).replace(/\s+/g, " ").slice(0, 100));
  await page.locator('input[aria-label="Select video files"]').setInputFiles([clip(6)]);
  await needs.locator('a[href^="/content/"]').waitFor({ timeout: 120000 }).catch(() => {});
  ok("reselected file resumed and completed", (await needs.locator('a[href^="/content/"]').count()) === 1);
  const sess6 = (await api(editorCtx, "GET", "/api/v1/media/uploads?limit=50")).json.filter((u) => u.original_filename === "w0-clip-6.mp4");
  ok("exactly one server session for the resumed file", sess6.length === 1 && sess6[0].status === "completed", JSON.stringify(sess6.map((u) => u.status)));
  await page.screenshot({ path: `${OUT}/06-resumed.png` });

  // ---- 6. My uploads
  await page.getByRole("tab", { name: "My uploads" }).click();
  const myList = page.getByRole("list", { name: "My uploads" });
  await myList.waitFor({ timeout: 15000 });
  const myText = await myList.innerText();
  ok("My uploads shows completed items pending review", (myText.match(/Pending review/g) || []).length >= 5, `${(myText.match(/Pending review/g) || []).length} pending badges`);
  await page.screenshot({ path: `${OUT}/07-my-uploads.png` });

  // ---- 7. library, thumbnail, preview
  const assets = (await api(editorCtx, "GET", "/api/v1/media/assets?ingest_source=upload&page_size=50")).json;
  ok("library lists the uploads", assets.total >= 5, `total=${assets.total}`);
  const first = assets.items.find((a) => a.title === "w0 clip 1") || assets.items[0];
  for (let i = 0; i < 30 && first.thumbnail_status !== "ready"; i++) { await sleep(2000); Object.assign(first, (await api(editorCtx, "GET", `/api/v1/media/assets/${first.id}`)).json); }
  ok("thumbnail rendered for an upload", first.thumbnail_status === "ready", first.thumbnail_status);
  await page.goto(`${BASE}/content/${first.id}`);
  const video = page.locator("video");
  await video.waitFor({ timeout: 30000 });
  const ready = await video.evaluate((v) => new Promise((res) => { if (v.readyState >= 1) return res(v.readyState); v.addEventListener("loadedmetadata", () => res(v.readyState), { once: true }); v.addEventListener("error", () => res(-1), { once: true }); setTimeout(() => res(v.readyState), 15000); }));
  ok("preview video loads metadata in the browser", ready >= 1, `readyState=${ready}`);
  const range = await editorCtx.request.get(`${BASE}${first.preview_url}`, { headers: { Range: "bytes=0-1023" } });
  ok("Range request through the proxy answers 206", range.status() === 206 && (range.headers()["content-range"] || "").startsWith("bytes 0-1023/"), `${range.status()} ${range.headers()["content-range"]}`);
  const poster = await video.getAttribute("poster");
  ok("thumbnail used as the preview poster on the asset page", Boolean(poster && poster.includes("/thumbnail")), String(poster));
  await page.screenshot({ path: `${OUT}/08-asset-page-editor.png`, fullPage: true });

  // ---- 8. editor restrictions
  const facetsReject = await api(editorCtx, "PUT", `/api/v1/media/assets/${first.id}/facets`, { production_owner_category_id: (await api(editorCtx, "GET", "/api/v1/content-categories")).json.find((c) => c.slug === "hao").id });
  ok("editor cannot reclassify to an owner they are not mapped to (403)", facetsReject.status === 403, String(facetsReject.status));
  const asHao = await api(editorCtx, "POST", "/api/v1/media/uploads", { filename: "x.mp4", size: 10, production_owner_category_id: (await api(editorCtx, "GET", "/api/v1/content-categories")).json.find((c) => c.slug === "hao").id, product_category_id: first.categories.find((c) => c.facet === "product_category").category_id, content_type_id: first.categories.find((c) => c.facet === "content_type").category_id });
  ok("editor cannot create an upload for an unmapped owner (403)", asHao.status === 403, String(asHao.status));
  const handoffAsEditor = await api(editorCtx, "POST", `/api/v1/media/assets/${first.id}/handoff`, { status: "approved", note: "nope" });
  ok("editor cannot approve (403)", handoffAsEditor.status === 403, String(handoffAsEditor.status));
  await page.goto(`${BASE}/reports/production`);
  await sleep(2000);
  ok("editor sees the admin-only notice on the production report", /limited to admins/i.test(await page.locator("body").innerText()));

  // ---- 9. admin rejects, editor corrects, admin approves
  const adminCtx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
  const admin = await adminCtx.newPage();
  await login(admin, ADMIN);
  await admin.goto(`${BASE}/content/${first.id}`);
  await admin.getByRole("button", { name: /^Reject/ }).first().click();
  await admin.fill("#handoff-note", "W0 acceptance: please re-export with the right LUT");
  await admin.getByRole("dialog").getByRole("button", { name: "Confirm" }).click();
  await admin.getByText("Rejected", { exact: true }).first().waitFor({ timeout: 15000 });
  ok("admin rejected the final in the UI", true);
  await admin.screenshot({ path: `${OUT}/09-rejected.png` });

  await page.goto(`${BASE}/uploads`);
  await page.getByRole("tab", { name: "My uploads" }).click();
  await page.getByRole("button", { name: "Submit corrected version" }).first().waitFor({ timeout: 15000 });
  const reviewerNote = await page.getByRole("list", { name: "My uploads" }).innerText();
  ok("editor sees the reviewer note in My uploads", /please re-export/.test(reviewerNote));
  await page.getByRole("button", { name: "Submit corrected version" }).first().click();
  await page.getByText(/Submitting a corrected version/).waitFor({ timeout: 10000 });
  await page.locator('input[aria-label="Select video files"]').setInputFiles([clip(5)]);
  const rowC = page.getByRole("list", { name: "Upload queue" }).locator("li", { hasText: "w0-clip-5.mp4" }).last();
  await rowC.locator('a[href^="/content/"]').waitFor({ timeout: 120000 }).catch(() => {});
  ok("corrected submission completed", (await rowC.locator('a[href^="/content/"]').count()) === 1);
  const corrected = (await api(editorCtx, "GET", "/api/v1/media/uploads?limit=50")).json.find((u) => u.replaces_asset_id === first.id);
  ok("corrected session links to the rejected asset", Boolean(corrected && corrected.status === "completed" && corrected.media_asset_id), JSON.stringify(corrected && { status: corrected.status }));
  await page.screenshot({ path: `${OUT}/10-corrected.png` });

  await admin.goto(`${BASE}/content/${corrected.media_asset_id}`);
  await admin.getByText(/Corrects|rejected original/).first().waitFor({ timeout: 15000 }).catch(() => {});
  await admin.getByRole("button", { name: /^Approve/ }).first().click();
  await admin.fill("#handoff-note", "W0 acceptance: approved");
  await admin.getByRole("dialog").getByRole("button", { name: "Confirm" }).click();
  await admin.getByText("Approved", { exact: true }).first().waitFor({ timeout: 15000 });
  ok("admin approved the corrected final in the UI", true);
  await admin.screenshot({ path: `${OUT}/11-approved.png` });

  // ---- 10. production report attribution
  await admin.goto(`${BASE}/reports/production`);
  await admin.getByRole("table").first().waitFor({ timeout: 30000 });
  await admin.getByText("Test Editor").first().waitFor({ timeout: 30000 }).catch(() => {});
  const report = await admin.locator("main, body").first().innerText();
  ok("report shows the editor and owner attribution", /Test Editor|editor@affilio.local/.test(report) && /Hang/.test(report), report.replace(/\s+/g, " ").slice(0, 120));
  await admin.screenshot({ path: `${OUT}/12-report.png`, fullPage: true });

  // ---- 11. audit coverage for handoff decisions
  const audit = (await api(adminCtx, "GET", "/api/v1/auth/audit?limit=200")).json || [];
  const actions = new Set(audit.map((e) => e.action));
  ok("handoff approval/rejection produce audit events", [...actions].some((a) => /handoff/.test(a)), `actions seen: ${[...actions].filter((a) => /handoff|upload|media/.test(a)).join(", ")}`);

  ok("no browser console errors on the editor pages", consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | ").slice(0, 200));
  fs.writeFileSync(`${OUT}/console-errors.txt`, consoleErrors.join("\n"));
  await browser.close();
  console.log(`failed_checks=${failed}`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error("RUN ERROR", e); process.exit(2); });
