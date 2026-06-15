#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse


DEFAULT_OUTPUTS = Path.cwd()
DEFAULT_OBQ = Path(os.environ.get("OB_REVIEW_OBQ", "obq.ps1"))
DEFAULT_PROBE_NOTE = os.environ.get("OB_REVIEW_PROBE_NOTE", "")
DEFAULT_CORRECTIONS_MIRROR = os.environ.get("OB_REVIEW_CORRECTIONS_MIRROR", "")


class ReviewError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm_text(text: str) -> str:
    return text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")


def decode_cli_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "mbcs", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def safe_rel(rel: str) -> str:
    value = rel.replace("\\", "/").strip().strip("/")
    if not value:
        raise ReviewError("缺少文件路径")
    if ":" in value:
        raise ReviewError(f"非法路径：{value}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ReviewError(f"非法路径：{value}")
    if not value.lower().endswith(".md"):
        raise ReviewError(f"只允许 Markdown：{value}")
    return value


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def run_obq(
    obq: Path,
    args: list[str],
    timeout: int = 120,
    command_timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    if not obq.exists():
        raise ReviewError(f"OB CLI 排队入口不存在：{obq}")
    runner_args: list[str] = []
    if command_timeout is not None:
        runner_args.extend(["-CommandTimeoutSeconds", str(command_timeout)])
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(obq),
        *runner_args,
        *args,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewError(f"OB CLI 超时：{' '.join(args[:2])}") from exc
    if proc.returncode != 0:
        detail = (decode_cli_bytes(proc.stderr or b"") or decode_cli_bytes(proc.stdout or b"")).strip()
        raise ReviewError(f"OB CLI 失败：{' '.join(args[:2])} {detail}".strip())
    proc.stdout = decode_cli_bytes(proc.stdout or b"")  # type: ignore[assignment]
    proc.stderr = decode_cli_bytes(proc.stderr or b"")  # type: ignore[assignment]
    return proc  # type: ignore[return-value]


def cli_read(obq: Path, rel: str, timeout: int = 90) -> str:
    return run_obq(obq, ["read", f"path={rel}"], timeout=timeout).stdout


def ensure_cli_ready(obq: Path, probe_note: str) -> None:
    run_obq(obq, ["version"], timeout=45)
    if probe_note:
        probe = cli_read(obq, probe_note, timeout=60)
        if not probe.strip():
            raise ReviewError(f"OB CLI 探针读取为空：{probe_note}")


def write_text_exact(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)


def cli_replace_eval(obq: Path, rel: str, content: str, input_path: Path) -> None:
    write_text_exact(input_path, content)
    code = f"""
(async () => {{
  const fs = require('fs');
  const targetPath = {json.dumps(rel, ensure_ascii=False)};
  const inputPath = {json.dumps(str(input_path), ensure_ascii=False)};
  const content = fs.readFileSync(inputPath, 'utf8').replace(/^\\uFEFF/, '');
  const file = app.vault.getAbstractFileByPath(targetPath);
  if (!file || file.children) {{
    throw new Error('target note not found: ' + targetPath);
  }}
  await app.vault.modify(file, content);
  const readback = await app.vault.read(file);
  return JSON.stringify({{ ok: readback === content, path: targetPath, length: readback.length }});
}})()
""".strip()
    run_obq(obq, ["eval", f"code={code}"], timeout=240, command_timeout=180)


def cli_read_until_match(
    obq: Path,
    rel: str,
    expected: str,
    attempts: int = 8,
    delay_seconds: float = 0.75,
) -> tuple[str, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    last = ""
    expected_norm = norm_text(expected)
    for attempt in range(1, attempts + 1):
        try:
            current = cli_read(obq, rel, timeout=90)
            last = current
            ok = norm_text(current) == expected_norm
            records.append(
                {
                    "attempt": attempt,
                    "length": len(current),
                    "sha256": sha256_text(current),
                    "ok": ok,
                }
            )
            if ok:
                return current, records
        except Exception as exc:
            records.append({"attempt": attempt, "error": str(exc), "ok": False})
        if attempt < attempts:
            time.sleep(delay_seconds)
    return last, records


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ReviewError(f"读取摘要失败：{path}") from exc
    if not isinstance(data, dict):
        raise ReviewError(f"摘要格式错误：{path}")
    return data


def page_to_package(root: Path, page_value: str) -> tuple[Path, str]:
    page = unquote(urlparse(page_value or "").path).lstrip("/")
    if not page:
        raise ReviewError("缺少页面路径")
    candidate = (root / page).resolve()
    if not is_inside(candidate, root):
        raise ReviewError("页面路径越界")
    package_dir = candidate.parent if candidate.suffix else candidate
    review_html = candidate.name if candidate.suffix else ""
    if not package_dir.exists():
        raise ReviewError(f"审核包不存在：{package_dir}")
    return package_dir, review_html


def find_item(summary: dict[str, Any], rel: str, review_html: str) -> tuple[int, dict[str, Any]]:
    items = summary.get("items") or []
    if not isinstance(items, list):
        raise ReviewError("summary.json 缺少 items")
    rel_norm = rel.replace("\\", "/").strip("/")
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_rel = str(item.get("rel_path") or item.get("path") or "").replace("\\", "/").strip("/")
        item_html = str(item.get("review_html") or "")
        if (rel_norm and item_rel == rel_norm) or (review_html and item_html == review_html):
            return idx, item
    for idx, item in enumerate(items):
        if isinstance(item, dict) and Path(rel_norm).name == str(item.get("name") or ""):
            return idx, item
    raise ReviewError(f"摘要里找不到文件：{rel_norm}")


def find_summary(package_dir: Path) -> tuple[Path, dict[str, Any]]:
    for name in ("summary.json", "review-summary.json"):
        path = package_dir / name
        if path.exists():
            return path, read_json(path)
    raise ReviewError(f"审核包缺少 summary.json：{package_dir}")


def proposed_path_for(package_dir: Path, item: dict[str, Any], rel: str) -> Path:
    proposed = str(item.get("proposed") or item.get("proposed_path") or "").replace("\\", "/").strip("/")
    if proposed:
        path = (package_dir / proposed).resolve()
    else:
        path = (package_dir / "proposed" / PurePosixPath(rel).name).resolve()
    if not is_inside(path, package_dir):
        raise ReviewError("新稿路径越界")
    if not path.exists():
        raise ReviewError(f"新稿不存在：{path}")
    return path


def next_review(summary: dict[str, Any], index: int, package_dir: Path | None = None) -> str | None:
    items = summary.get("items") or []
    if not isinstance(items, list):
        return None
    for nxt in items[index + 1 :]:
        if isinstance(nxt, dict) and nxt.get("review_html") and item_needs_review(nxt):
            return str(nxt["review_html"])
    if package_dir is not None:
        for name in ("00-文件夹新旧对比.html", "00-OB-review-index.html"):
            if (package_dir / name).exists():
                return name
    return "00-文件夹新旧对比.html"


def item_needs_review(item: dict[str, Any]) -> bool:
    if structure_review_count(item):
        return True
    changed = item.get("changed")
    if isinstance(changed, bool) and changed:
        return True
    for key in ("line_delta", "added_lines", "removed_lines"):
        try:
            if int(item.get(key) or 0):
                return True
        except (TypeError, ValueError):
            pass
    old_chars = item.get("old_chars")
    new_chars = item.get("new_chars")
    if old_chars is not None and new_chars is not None:
        try:
            if int(old_chars) != int(new_chars):
                return True
        except (TypeError, ValueError):
            pass
    changes = item.get("changes") or []
    if isinstance(changes, str):
        changes = [changes]
    if isinstance(changes, list):
        ignored = {"", "未改", "无", "保护"}
        return any(str(change).strip() not in ignored for change in changes)
    return False


def structure_review_count(item: dict[str, Any]) -> int:
    total = 0
    for key in ("structure", "structure_changes"):
        value = item.get(key)
        if isinstance(value, list):
            total += len(value)
    relations = item.get("structure_relations")
    if isinstance(relations, dict):
        try:
            total += int(relations.get("count") or 0)
        except (TypeError, ValueError):
            pass
        for key in ("renamed", "moved", "merged", "split", "folder_changed"):
            value = relations.get(key)
            if isinstance(value, list):
                total += len(value)
        if relations.get("changed") and total == 0:
            total = 1
    return total


class ReviewActions:
    def __init__(
        self,
        root: Path,
        obq: Path,
        probe_note: str,
        corrections_mirror: Path | None = None,
    ) -> None:
        self.root = root
        self.obq = obq
        self.probe_note = probe_note
        self.corrections_mirror = corrections_mirror
        self.lock = threading.Lock()
        self.log_root = root / "review-apply-log"
        self.correction_path = root / "review-corrections" / "pending.jsonl"

    def queue(self, payload: dict[str, Any], status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {
            "time": now_iso(),
            "status": status,
            "source": "ob-review-server",
            "folder": payload.get("folder") or "",
            "file": payload.get("file") or payload.get("path") or "",
            "decision": payload.get("decision") or "",
            "note": payload.get("note") or "",
            "page": payload.get("page") or "",
            "scope": payload.get("scope") or "content",
            "kind": payload.get("kind") or "",
            "old_path": payload.get("old_path") or "",
            "new_path": payload.get("new_path") or "",
        }
        if extra:
            record.update(extra)
        append_jsonl(self.correction_path, record)
        if self.corrections_mirror is not None:
            append_jsonl(self.corrections_mirror, record)
        return record

    def apply_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        rel = safe_rel(str(payload.get("file") or payload.get("path") or ""))
        package_dir, review_html = page_to_package(self.root, str(payload.get("page") or ""))
        summary_path, summary = find_summary(package_dir)
        index, item = find_item(summary, rel, review_html)
        proposed_path = proposed_path_for(package_dir, item, rel)
        new_text = proposed_path.read_text(encoding="utf-8-sig")
        run_id = stamp()
        backup_dir = self.log_root / "backup-cli-read" / run_id
        backup_file = backup_dir / rel.replace("/", os.sep)
        readback_file = self.log_root / "readback-cli-read" / run_id / rel.replace("/", os.sep)
        input_file = self.log_root / "eval-input" / run_id / rel.replace("/", os.sep)
        restore_readback_file = self.log_root / "restore-readback-cli-read" / run_id / rel.replace("/", os.sep)
        audit = {
            "time": now_iso(),
            "run_id": run_id,
            "rel_path": rel,
            "folder": payload.get("folder") or "",
            "page": payload.get("page") or "",
            "package_dir": str(package_dir),
            "summary": str(summary_path),
            "proposed": str(proposed_path),
            "new_sha256": sha256_text(new_text),
        }
        with self.lock:
            ensure_cli_ready(self.obq, self.probe_note)
            old_text = cli_read(self.obq, rel, timeout=90)
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            write_text_exact(backup_file, old_text)
            already_applied = norm_text(old_text) == norm_text(new_text)
            if already_applied:
                readback = old_text
                readback_attempts = [
                    {
                        "attempt": 1,
                        "length": len(readback),
                        "sha256": sha256_text(readback),
                        "ok": True,
                    }
                ]
            else:
                cli_replace_eval(self.obq, rel, new_text, input_file)
                readback, readback_attempts = cli_read_until_match(self.obq, rel, new_text)
            readback_file.parent.mkdir(parents=True, exist_ok=True)
            write_text_exact(readback_file, readback)
            verify_ok = norm_text(readback) == norm_text(new_text)
            audit.update(
                {
                    "backup": str(backup_file),
                    "readback": str(readback_file),
                    "eval_input": str(input_file),
                    "already_applied": already_applied,
                    "old_sha256": sha256_text(old_text),
                    "readback_sha256": sha256_text(readback),
                    "readback_attempts": readback_attempts,
                    "verify_ok": verify_ok,
                }
            )
            if not verify_ok:
                audit["restore_attempted"] = True
                try:
                    restore_input_file = self.log_root / "restore-eval-input" / run_id / rel.replace("/", os.sep)
                    cli_replace_eval(self.obq, rel, old_text, restore_input_file)
                    restored, restore_attempts = cli_read_until_match(self.obq, rel, old_text)
                    restore_readback_file.parent.mkdir(parents=True, exist_ok=True)
                    write_text_exact(restore_readback_file, restored)
                    audit.update(
                        {
                            "restore_input": str(restore_input_file),
                            "restore_readback": str(restore_readback_file),
                            "restore_readback_sha256": sha256_text(restored),
                            "restore_attempts": restore_attempts,
                            "restore_ok": norm_text(restored) == norm_text(old_text),
                        }
                    )
                except Exception as exc:
                    audit.update({"restore_ok": False, "restore_error": str(exc)})
            append_jsonl(self.log_root / "applied.jsonl", audit)
        if not audit["verify_ok"]:
            restored = "，已恢复旧稿" if audit.get("restore_ok") else "，恢复旧稿失败"
            raise ReviewError(f"写入后校验不一致：{rel}{restored}")
        return {
            "ok": True,
            "decision": "通过",
            "status": "已写入",
            "file": rel,
            "next": next_review(summary, index, package_dir),
            "audit": audit,
        }

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        note = str(payload.get("note") or "").strip()
        scope = str(payload.get("scope") or "content")
        if scope == "structure":
            self.queue(payload, "已入队")
            return {"ok": True, "decision": "指正" if note else "通过", "status": "已入队", "next": None}
        if note:
            package_dir = ""
            proposed = ""
            try:
                rel = safe_rel(str(payload.get("file") or payload.get("path") or ""))
                pkg, review_html = page_to_package(self.root, str(payload.get("page") or ""))
                _, summary = find_summary(pkg)
                item_index, item = find_item(summary, rel, review_html)
                package_dir = str(pkg)
                proposed = str(proposed_path_for(pkg, item, rel))
                next_page = next_review(summary, item_index, pkg)
            except Exception:
                next_page = None
            self.queue(payload, "已入队", {"package_dir": package_dir, "proposed": proposed})
            return {"ok": True, "decision": "指正", "status": "已入队", "next": next_page}
        return self.apply_content(payload)

    def log_error(self, payload: dict[str, Any] | None, error: str) -> None:
        record = {
            "time": now_iso(),
            "source": "ob-review-server",
            "error": error,
            "payload": payload or {},
        }
        append_jsonl(self.log_root / "errors.jsonl", record)


class ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, root: Path, actions: ReviewActions, **kwargs: Any) -> None:
        self.actions = actions
        super().__init__(*args, directory=str(root), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/review":
            self.send_error(404)
            return
        payload: dict[str, Any] | None = None
        try:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length)
            parsed = json.loads(raw.decode("utf-8-sig"))
            payload = parsed if isinstance(parsed, dict) else None
            if not isinstance(payload, dict):
                raise ReviewError("请求格式错误")
            result = self.actions.handle(payload)
            self.write_json(200, result)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            try:
                self.actions.log_error(payload, message)
            except Exception:
                pass
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} api-error {message}", flush=True)
            self.write_json(400, {"ok": False, "error": message})

    def write_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts} {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve OB review pages and apply approved notes through Obsidian CLI.")
    parser.add_argument("--root", default=str(DEFAULT_OUTPUTS), type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8791, type=int)
    parser.add_argument(
        "--obq",
        default=str(DEFAULT_OBQ),
        type=Path,
        help="Path to the queued Obsidian CLI wrapper. Can also be set with OB_REVIEW_OBQ.",
    )
    parser.add_argument(
        "--probe-note",
        default=DEFAULT_PROBE_NOTE,
        help="Optional note to read before applying approved content. Can also be set with OB_REVIEW_PROBE_NOTE.",
    )
    parser.add_argument(
        "--corrections-mirror",
        default=DEFAULT_CORRECTIONS_MIRROR,
        help="Optional extra pending.jsonl path for hourly maintainers.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.exists():
        raise SystemExit(f"Output root not found: {root}")
    mirror = Path(args.corrections_mirror).resolve() if args.corrections_mirror else None
    actions = ReviewActions(root, args.obq.resolve(), args.probe_note, mirror)
    handler = partial(ReviewHandler, root=root, actions=actions)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"OB review server: http://{args.host}:{args.port}/  root={root}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
