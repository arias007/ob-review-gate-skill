# ob-review-gate-skill

Human review gate for AI-assisted Obsidian vault organization.

This skill builds a local, mobile-friendly HTML review package before an agent writes, renames, moves, merges, splits, relinks, or restructures notes. It is designed for workflows where an AI proposes changes and a human approves or corrects them one file at a time.

## What It Does

- Generates review pages from a JSON manifest.
- Shows file-internal changes with line-numbered red/green diffs.
- Shows old and proposed Markdown side by side, with source/rendered modes.
- Shows structure candidates separately with square markers for:
  - rename
  - move
  - merge
  - split
  - folder reorganization
- Serves an optional local API for real approvals.
- Applies approved content through a configured Obsidian CLI wrapper only.
- Queues corrections to `review-corrections/pending.jsonl`.
- Keeps structure changes review-only by default.

## Safety Model

The package generation phase is read-only. It does not modify the vault.

The interactive server only writes when all of these are true:

- The page is served by `scripts/ob_review_server.py`.
- The reviewer presses the edit icon with an empty correction box.
- The item is a content item, not a structure-only item.
- A working Obsidian CLI wrapper is configured with `--obq` or `OB_REVIEW_OBQ`.
- The server can read back and verify the final content.

If the reviewer enters correction text, the server queues the correction and does not write the vault.

## Manifest Format

```json
{
  "title": "OB Review",
  "vault_label": "note",
  "items": [
    {
      "path": "Folder/Note.md",
      "old_text": "# Old",
      "new_text": "# New",
      "changes": ["content", "links", "tags"],
      "links": {
        "added": ["Other Note"],
        "removed": [],
        "current": ["Existing Note"],
        "suggested": ["Suggested Note"]
      },
      "structure": [
        {
          "kind": "rename",
          "old_path": "Folder/Old Name.md",
          "new_path": "Folder/New Name.md",
          "reason": "Shorter searchable name"
        }
      ]
    }
  ]
}
```

`kind` for structure candidates must be one of:

```text
rename, move, merge, split, folder
```

Markdown link additions/removals are file-internal changes, not structure changes.

## Build A Review Package

```powershell
python scripts/build_ob_review_package.py --manifest examples/sample-manifest.json --out tmp/sample-review
```

Open:

```text
tmp/sample-review/00-OB-review-index.html
```

Static HTML can display review pages and cache local UI state. It cannot apply approvals to a vault.

## Serve Interactive Review

Use the interactive server when approval clicks should be real:

```powershell
python scripts/ob_review_server.py --root tmp/sample-review --host 127.0.0.1 --port 8791 --obq <path-to-obq.ps1>
```

You can also configure the CLI wrapper with an environment variable:

```powershell
$env:OB_REVIEW_OBQ = "C:\path\to\obq.ps1"
python scripts/ob_review_server.py --root tmp/sample-review --host 127.0.0.1 --port 8791
```

Useful options:

```text
--root                 Review package output directory.
--host                 Bind host. Use 0.0.0.0 for LAN/mobile testing.
--port                 HTTP port.
--obq                 Queued Obsidian CLI wrapper.
--probe-note           Optional note to read before applying approvals.
--corrections-mirror   Optional extra pending.jsonl mirror path.
```

## Review Semantics

In the review UI:

- Empty correction box + edit icon = approve content and apply proposed Markdown.
- Non-empty correction box + edit icon = queue correction, no vault write.
- No action = skipped/rejected for now.
- Square-marked structure candidates are queued/reviewed, not applied by the server.

After a successful content approval, the server returns the next changed review page and skips unchanged/no-diff files.

## Validation

```powershell
python -m py_compile scripts/build_ob_review_package.py scripts/ob_review_server.py
python scripts/build_ob_review_package.py --manifest examples/sample-manifest.json --out tmp/sample-review
```

Expected output:

- `tmp/sample-review/00-OB-review-index.html`
- one or more file review pages
- `summary.json`
- square markers for structure candidates

## Files

```text
SKILL.md
scripts/build_ob_review_package.py
scripts/ob_review_server.py
examples/sample-manifest.json
```

## Notes

This repository intentionally does not include real vault output, generated review packages, private notes, audit logs, or corrections from a live vault. Keep those in local ignored output directories.
