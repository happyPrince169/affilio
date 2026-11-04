#!/usr/bin/env python3
"""Phase D smoke test driver for the editor-upload pipeline (ADR 0012 amendment 2).

Runs the end-to-end flow against a live AFFILIO deployment through its public HTTP API only,
exactly as the browser would, and prints one `ok`/`FAIL` line per check. Standard library
only, so it also runs with the NAS `python3`. Passwords are read with `getpass`, never taken
from arguments or the environment.

    uv run python scripts/smoke_upload.py --base https://app.example.online \\
        --file smoke-affilio.mp4 --corrected smoke-affilio-corrected.mp4 \\
        --editor-email editor@example.com --admin-email admin@example.com

Flow: editor signs in → upload options → create session → chunked upload with per-chunk
SHA-256 → complete → poll until finalized → asset provenance (uploads root) → thumbnail →
Range preview and full-stream SHA-256 → library filters → duplicate re-upload → admin
rejects → editor submits the corrected clip as a revision → admin reclassifies (file path
unchanged) → admin approves → export/publishing input state. Nothing here touches the NAS
filesystem directly; the companion read-only checks are in docs/deployment/upload-rollout.md
(D.6). Exit code 0 only when every check passed.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "/api/v1"


class Client:
    """Minimal cookie-carrying HTTP client. The session cookie is forwarded explicitly so a
    `Secure` cookie still works when the test runs over plain HTTP on the LAN."""

    def __init__(self, base: str, org_slug: str | None) -> None:
        self.base = base.rstrip("/")
        self.cookie: str | None = None
        self.org_slug = org_slug

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        url = self.base + path
        body = data
        hdrs = {"Accept": "application/json", **(headers or {})}
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        if self.org_slug:
            hdrs["X-Organization-Slug"] = self.org_slug
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - operator URL
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()

    def json(self, method: str, path: str, **kw) -> tuple[int, dict | list | None]:
        status, _, body = self.request(method, path, **kw)
        try:
            return status, json.loads(body) if body else None
        except json.JSONDecodeError:
            return status, {"raw": body[:200].decode("utf-8", "replace")}

    def login(self, email: str, password: str) -> dict:
        status, headers, body = self.request(
            "POST", f"{API}/auth/login", json_body={"email": email, "password": password}
        )
        if status != 200:
            raise SystemExit(f"login failed for {email}: {status} {body[:200]!r}")
        set_cookie = headers.get("set-cookie", "")
        self.cookie = set_cookie.split(";", 1)[0]
        if not self.cookie:
            raise SystemExit("login answered without a session cookie")
        return json.loads(body)


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
        if not ok:
            self.failed += 1
        return ok


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def upload_file(
    client: Client,
    rep: Report,
    path: str,
    *,
    owner_id: str,
    product_id: str,
    type_id: str,
    replaces: str | None,
    label: str,
    poll_timeout: float,
) -> dict:
    size = os.path.getsize(path)
    digest = sha256_file(path)
    body = {
        "filename": os.path.basename(path),
        "size": size,
        "production_owner_category_id": owner_id,
        "product_category_id": product_id,
        "content_type_id": type_id,
        "client_sha256": digest,
    }
    if replaces:
        body["replaces_asset_id"] = replaces
    status, created = client.json("POST", f"{API}/media/uploads", json_body=body)
    rep.check(f"{label}: session created", status == 201, f"{status} {created}")
    if status != 201:
        return {}
    upload_id, chunk = created["id"], created["chunk_bytes"]
    offset = 0
    with open(path, "rb") as f:
        while offset < size:
            piece = f.read(chunk)
            status, _, resp = client.request(
                "PATCH",
                f"{API}/media/uploads/{upload_id}/chunk",
                data=piece,
                headers={
                    "Content-Type": "application/offset+octet-stream",
                    "Upload-Offset": str(offset),
                    "X-Upload-Chunk-Sha256": hashlib.sha256(piece).hexdigest(),
                },
            )
            if status != 200:
                rep.check(f"{label}: chunk at {offset}", False, f"{status} {resp[:200]!r}")
                return {"id": upload_id}
            offset = json.loads(resp)["committed_offset"]
    rep.check(f"{label}: all bytes committed", offset == size, f"{offset}/{size}")
    status, completed = client.json("POST", f"{API}/media/uploads/{upload_id}/complete")
    rep.check(f"{label}: complete accepted", status == 202, f"{status}")
    deadline = time.monotonic() + poll_timeout
    state = completed or {}
    while time.monotonic() < deadline and state.get("status") in {
        "uploaded",
        "verifying",
        "finalizing",
    }:
        time.sleep(3)
        _, state = client.json("GET", f"{API}/media/uploads/{upload_id}")
    state = state or {}
    state["_sha256"] = digest
    state["_size"] = size
    print(f"     {label}: final status={state.get('status')} code={state.get('failure_code')}")
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="web origin, e.g. https://app.example.online")
    ap.add_argument("--file", required=True, help="synthetic clip for the first upload")
    ap.add_argument("--corrected", required=True, help="second synthetic clip (revision)")
    ap.add_argument("--editor-email", help="mapped editor; defaults to the admin account")
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--org-slug", default=None)
    ap.add_argument("--owner", default=None, help="owner slug to upload for (default: first)")
    ap.add_argument("--poll-timeout", type=float, default=240.0)
    ap.add_argument("--legacy-finals", type=int, default=92, help="finals expected before the run")
    args = ap.parse_args()
    rep = Report()

    admin = Client(args.base, args.org_slug)
    admin_pw = getpass.getpass(f"password for {args.admin_email}: ")
    admin_me = admin.login(args.admin_email, admin_pw)
    print(f"     admin signed in as {admin_me['email']} ({admin_me['organization']['role']})")
    if args.editor_email and args.editor_email != args.admin_email:
        editor = Client(args.base, args.org_slug)
        editor.login(args.editor_email, getpass.getpass(f"password for {args.editor_email}: "))
    else:
        print("     no separate editor account: the admin uploads (admin path, any owner)")
        editor = admin

    status, options = editor.json("GET", f"{API}/media/uploads/options")
    if status != 200:
        rep.check("upload options", False, f"{status} {options}")
        return 1
    owners = options["owners"]
    if args.owner:
        owners = [o for o in owners if o["slug"] == args.owner]
    rep.check("uploader has at least one production owner", bool(owners), str([o["slug"] for o in owners]))
    if not owners:
        return 1
    owner = owners[0]
    product = options["product_categories"][0]
    ctype = options["content_types"][0]
    print(f"     owner={owner['slug']} product={product['slug']} type={ctype['slug']} chunk={options['limits']['chunk_bytes']}")

    # 1-2. upload, finalize, SHA-256
    first = upload_file(
        editor, rep, args.file, owner_id=owner["id"], product_id=product["id"], type_id=ctype["id"],
        replaces=None, label="upload 1", poll_timeout=args.poll_timeout,
    )
    if not rep.check("upload 1 completed", first.get("status") == "completed", str(first.get("failure_detail"))):
        return 1
    asset_id = first["media_asset_id"]
    status, asset = admin.json("GET", f"{API}/media/assets/{asset_id}")
    rep.check("asset readable", status == 200)
    rep.check("asset is an uploaded final", asset.get("asset_kind") == "final" and asset.get("ingest_source") == "upload")
    rep.check("asset lives on the uploads root", asset.get("root_key") == "uploads", str(asset.get("root_key")))
    rep.check("physical path is ready/<org>/<YYYY>/<MM>/<upload id>.mp4",
              (asset.get("relative_path") or "").startswith("ready/") and asset["relative_path"].endswith(f"{first['id']}.mp4"),
              str(asset.get("relative_path")))
    rep.check("pending review", asset.get("handoff_status") == "pending_review")
    facets = {c["facet"]: c for c in asset.get("categories", []) if c.get("facet")}
    rep.check("owner/category/type stored as upload assignments",
              {f: facets[f]["assignment_source"] for f in facets} == {"production_owner": "upload", "product_category": "upload", "content_type": "upload"},
              str({f: (c["slug"], c["assignment_source"]) for f, c in facets.items()}))
    rep.check("stored content hash equals the file's SHA-256",
              any(v.get("content_hash") == first["_sha256"] for v in asset.get("variants", []) if v["kind"] == "final"))
    print(f"     relative_path={asset.get('relative_path')}")

    # 4. thumbnail
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and asset.get("thumbnail_status") == "pending":
        time.sleep(5)
        _, asset = admin.json("GET", f"{API}/media/assets/{asset_id}")
    rep.check("thumbnail ready", asset.get("thumbnail_status") == "ready", str(asset.get("thumbnail_error")))
    status, headers, body = admin.request("GET", f"{API}/media/assets/{asset_id}/thumbnail", headers={"Accept": "image/jpeg"})
    rep.check("thumbnail served", status == 200 and headers.get("content-type", "").startswith("image/jpeg") and len(body) > 0, f"{status} {len(body)} bytes")

    # 5. preview: Range and full stream
    rep.check("preview_url present", bool(asset.get("preview_url")))
    status, headers, body = admin.request("GET", f"{API}/media/assets/{asset_id}/stream", headers={"Range": "bytes=0-1023", "Accept": "*/*"})
    with open(args.file, "rb") as f:
        head = f.read(1024)
    rep.check("Range request answers 206 with the first KiB", status == 206 and body == head and headers.get("content-range", "").startswith("bytes 0-1023/"), f"{status} {headers.get('content-range')}")
    status, headers, body = admin.request("GET", f"{API}/media/assets/{asset_id}/stream", headers={"Accept": "*/*"})
    rep.check("full stream SHA-256 equals the uploaded file", status == 200 and hashlib.sha256(body).hexdigest() == first["_sha256"], f"{status} {len(body)} bytes")

    # 6. library
    status, page = admin.json("GET", f"{API}/media/assets?" + urllib.parse.urlencode({"ingest_source": "upload", "production_owner_id": owner["id"], "page_size": 100}))
    rep.check("library lists the upload under its owner and source", status == 200 and any(a["id"] == asset_id for a in page["items"]))
    status, finals = admin.json("GET", f"{API}/media/assets?asset_kind=final&page_size=1")
    rep.check("unified library counts legacy finals and the upload together", status == 200 and finals["total"] >= args.legacy_finals + 1, f"total={finals.get('total')}")

    # 7. duplicate
    dup = upload_file(editor, rep, args.file, owner_id=owner["id"], product_id=product["id"], type_id=ctype["id"], replaces=None, label="duplicate", poll_timeout=args.poll_timeout)
    rep.check("duplicate content rejected without a new asset", dup.get("status") == "duplicate" and dup.get("media_asset_id") is None, str(dup.get("failure_detail")))
    status, finals_after = admin.json("GET", f"{API}/media/assets?asset_kind=final&page_size=1")
    rep.check("asset count unchanged by the duplicate", finals_after.get("total") == finals.get("total"))

    # 8. reject and corrected submission
    status, rejected = admin.json("POST", f"{API}/media/assets/{asset_id}/handoff", json_body={"status": "rejected", "note": "smoke test: please re-export"})
    rep.check("admin rejected the first upload", status == 200 and rejected.get("handoff_status") == "rejected", f"{status} {rejected if status != 200 else ''}")
    corrected = upload_file(editor, rep, args.corrected, owner_id=owner["id"], product_id=product["id"], type_id=ctype["id"], replaces=asset_id, label="corrected", poll_timeout=args.poll_timeout)
    if not rep.check("corrected upload completed", corrected.get("status") == "completed", str(corrected.get("failure_detail"))):
        return 1
    new_id = corrected["media_asset_id"]
    status, new_asset = admin.json("GET", f"{API}/media/assets/{new_id}")
    rep.check("revision links to the rejected original", new_asset.get("revision_of_asset_id") == asset_id)

    # 9. reclassify metadata only
    status, admin_options = admin.json("GET", f"{API}/media/uploads/options")
    other_owner = next((o for o in admin_options["owners"] if o["id"] != owner["id"]), None)
    other_type = next((t for t in admin_options["content_types"] if t["id"] != ctype["id"]), None)
    change = {"production_owner_category_id": other_owner["id"]} if other_owner else ({"content_type_id": other_type["id"]} if other_type else None)
    if change:
        before_path = new_asset.get("relative_path")
        status, reclassified = admin.json("PUT", f"{API}/media/assets/{new_id}/facets", json_body=change)
        rep.check("reclassification accepted", status == 200, f"{status} {reclassified if status != 200 else ''}")
        rep.check("physical path unchanged after reclassification", reclassified.get("relative_path") == before_path)
        new_facets = {c["facet"]: c["slug"] for c in reclassified.get("categories", []) if c.get("facet")}
        print(f"     facets after reclassification: {new_facets}")
    else:
        print("     reclassification skipped: only one owner and one content type exist")

    # 10. approve and publishing/export input
    status, approved = admin.json("POST", f"{API}/media/assets/{new_id}/handoff", json_body={"status": "approved", "note": "smoke test: approved"})
    rep.check("admin approved the corrected final", status == 200 and approved.get("handoff_status") == "approved", f"{status} {approved if status != 200 else ''}")
    status, export = admin.json("GET", f"{API}/media/assets/{new_id}/export")
    rep.check("export/publishing input resolves on the uploads root", status == 200 and export is not None and export.get("state") in {"not_needed", "not_generated", "ready", "queued", "processing"}, f"{status} {export}")

    print()
    print(json.dumps({
        "first_upload_id": first["id"], "first_asset_id": asset_id, "first_relative_path": asset.get("relative_path"),
        "duplicate_upload_id": dup.get("id"), "corrected_upload_id": corrected["id"], "corrected_asset_id": new_id,
        "corrected_relative_path": new_asset.get("relative_path"), "sha256": {"first": first["_sha256"], "corrected": corrected["_sha256"]},
        "failed_checks": rep.failed,
    }, indent=1))
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
