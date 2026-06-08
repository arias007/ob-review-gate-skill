---
name: ob-review-gate
description: Use when an agent needs to organize an Obsidian vault safely by generating a human-reviewable HTML package before any write, move, rename, merge, split, link repair, or folder restructure. Builds mobile-first old/new/diff review pages plus square-marked structure-change approvals for rename, move, merge, split, and folder reorganization.
---

# OB Review Gate

Generate a review gate for Obsidian vault organization before changing the vault.

## Core Rule

Never write, move, rename, merge, split, delete, or repair links in the vault during this skill's review-package phase. First produce a local HTML review package, let the user audit the proposed changes, then apply only approved changes using the user's required Obsidian write path.

## Inputs

- Vault path: the Obsidian vault to scan.
- Output path: where to write the review package.
- Scope: folders/files/tags to include.
- Organizer preferences: note length, naming, folder structure, link rules, plugin-protection rules, sensitive-content policy.

## Workflow

1. Read preferences and constraints.
   - Identify vault path, protected folders, plugin syntax, and write confirmation rules.
   - Treat encrypted/sensitive areas according to the user's explicit policy. Do not invent redaction labels.

2. Scan read-only.
   - Read Markdown files and gather path, content, links, tags, frontmatter, line counts, plugin markers, and candidate folder relationships.
   - Do not modify the vault.

3. Propose changes as data.
   - File-internal changes: content polish, heading cleanup, tag/link additions, formatting, note splitting suggestions.
   - Structure changes: only these five categories:
     - rename
     - move
     - merge
     - split
     - folder
   - Markdown link additions/removals/current/suggested links are file-internal link changes, not structure changes.

4. Build a manifest.
   - Use `scripts/build_ob_review_package.py --manifest <manifest.json> --out <output-dir>`.
   - Manifest items must contain old and proposed text for file-internal review.
   - Structure changes must include old path, new path, kind, reason, and related files.

5. Verify the package.
   - Open `00-OB-review-index.html`.
   - Mobile viewport must work.
   - Files with square markers `□N` must open structure-change review only, not old/new content.
   - Normal content changes must show diff first, then old/new source or rendered panes.
   - The review input uses one icon: blank `✎` means approved; text plus `✎` means correction; no action means skipped/rejected.

6. Serve the interactive gate when approvals should execute.
   - Static HTML can only display/cache review state.
   - To make `✎` real, serve the package with `scripts/ob_review_server.py`.
   - Blank `✎` on a content file means approved: replace the old note with the proposed note through the configured Obsidian CLI wrapper, then jump to the next file.
   - Text plus `✎` means correction: queue the correction, do not write the vault.
   - Structure changes are review-only queue entries; do not rename, move, merge, split, or reorganize folders from this server.

7. Apply structure changes later, separately.
   - Only after explicit user approval of the queued structure plan, apply approved changes through the user's required Obsidian write path.
   - Keep backups, small batches, audit logs, and rollback metadata.

## Interactive Server

Run from the skill folder or pass absolute paths:

```powershell
python scripts/ob_review_server.py --root <output-dir> --host 0.0.0.0 --port 8791 --obq C:/Users/35007/Documents/Codex/tools/ob-cli-queue/obq.ps1
```

Useful options:

- `--root`: review package output directory, served as the web root.
- `--obq`: queued Obsidian CLI wrapper. The default also honors `OB_REVIEW_OBQ`.
- `--probe-note`: note read before applying approved content; default `AI/AI入口.md`.
- `--corrections-mirror`: optional extra `pending.jsonl` path for hourly maintainers.

Server behavior:

- `POST /api/review` with empty `note` and `scope:"content"` applies the proposed Markdown through the queued Obsidian CLI only.
  - Do not use `create content=` plus `append inline` for review approval writes; that path can leave a note empty or partly written when CLI argument escaping fails.
  - The server writes the proposed text to an audit-side temporary file, then calls Obsidian `eval` through `obq.ps1` so Obsidian itself reads that temporary file and runs `app.vault.modify(file, content)`.
  - After writing, the server retries CLI readback and compares normalized text/hash before returning success and jumping to the next page.
  - If verification fails, the server attempts to restore the backed-up old text through the same `eval` path before returning a 400.
- `POST /api/review` with non-empty `note` queues a correction under `<output-dir>/review-corrections/pending.jsonl`.
- Successful content applies CLI-read backups, eval input files, readbacks, hashes, retry records, and `review-apply-log/applied.jsonl`.
- Failed API requests are written to `review-apply-log/errors.jsonl` with the payload and concise error.
- Old `localStorage` review states are UI hints only; never replay them as approvals.

## Review UI Rules

- Mobile first.
- Top-level tree root should be the vault root label, often `note`.
- Show all review-scope files in their folder relationship.
- Folder labels show changed files and structure-change counts.
- File labels show content line delta and `□N` for structure changes.
- Clicking a `□` file or its `□` button opens only the structure-change review panel.
- Structure-change review shows old path/name, new path/name, kind, reason, and reviewer input.
- Do not show file-internal old/new/diff for structure-change-only entries.
- Content diff uses red removed lines, green added lines, and line numbers.
- Old/new panes support source and rendered Markdown modes together.
- Controls are compact; content is the priority.

## Manifest Schema

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

## Validation

Run:

```powershell
python scripts/build_ob_review_package.py --manifest examples/sample-manifest.json --out tmp/sample-review
python -m py_compile scripts/build_ob_review_package.py scripts/ob_review_server.py
```

Success:

- `tmp/sample-review/00-OB-review-index.html` exists.
- The index contains `□` structure markers.
- Structure files open a structure review panel and do not switch to content diff.
- Content files show diff, old, and new panes.
- Interactive approval requires the local server; test corrections with non-empty `note` first to avoid accidental vault writes.
- Before asking the user to retry a failed approval, run one end-to-end server test yourself against an already-approved content item or a deliberate probe note, then verify current vault text equals the proposed text by hash.
- The approval success response must include `ok:true`, `status:"已写入"`, `audit.verify_ok:true`, and `next` pointing to the next review page.
