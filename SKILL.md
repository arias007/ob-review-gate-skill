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

6. Apply later, separately.
   - Only after user approval, apply approved changes through the user's required Obsidian write path.
   - Keep backups, small batches, audit logs, and rollback metadata.

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
```

Success:

- `tmp/sample-review/00-OB-review-index.html` exists.
- The index contains `□` structure markers.
- Structure files open a structure review panel and do not switch to content diff.
- Content files show diff, old, and new panes.
