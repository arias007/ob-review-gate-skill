#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse


DEFAULT_OUTPUTS = Path.cwd()
DEFAULT_OBQ = Path(
    os.environ.get("OB_REVIEW_OBQ", r"C:\Users\35007\Documents\Codex\tools\ob-cli-queue\obq.ps1")
)
DEFAULT_PROBE_NOTE = os.environ.get("OB_REVIEW_PROBE_NOTE", "AI/AI入口.md")
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


def run_obq(obq: Path, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    if not obq.exists():
        raise ReviewError(f"OB CLI 排队入口不存在：{obq}")
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(obq),
        *args,
    ]
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewError(f"OB CLI 超时：{' '.join(args[:2])}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ReviewError(f"OB CLI 失败：{' '.join(args[:2])} {detail}".strip())
    return proc


def cli_read(obq: Path, rel: str, timeout: int = 90) -> str:
    return run_obq(obq, ["read", f"path={rel}"], timeout=timeout).stdout


def ensure_cli_ready(obq: Path, probe_note: str) -> None:
    run_obq(obq, ["version"], timeout=45)
    probe = cli_read(obq, probe_note, timeout=60)
    if not probe.strip():
        raise ReviewError(f"OB CLI 探针读取为空：{probe_note}")


def cli_replace(obq: Path, rel: str, content: str) -> None:
    run_obq(obq, ["create", f"path={rel}", "overwrite", "content="], timeout=90)
    if not content:
        return
    chunk_size = 6000
    for index in range(0, len(content), chunk_size):
        chunk = content[index : index + chunk_size]
        run_obq(obq, ["append", f"path={rel}", "inline", f"content={chunk}"], timeout=150)


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


def next_review(summary: dict[str, Any], index: int) -> str | None:
    items = summary.get("items") or []
    if not isinstance(items, list):
        return None
    for nxt in items[index + 1 :]:
        if isinstance(nxt, dict) and nxt.get("review_html"):
            return str(nxt["review_html"])
    return "00-文件夹新旧对比.html"


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
            backup_file.write_text(old_text, encoding="utf-8")
            cli_replace(self.obq, rel, new_text)
            readback = cli_read(self.obq, rel, timeout=90)
            readback_file.parent.mkdir(parents=True, exist_ok=True)
            readback_file.write_text(readback, encoding="utf-8")
            audit.update(
                {
                    "backup": str(backup_file),
                    "readback": str(readback_file),
                    "old_sha256": sha256_text(old_text),
                    "readback_sha256": sha256_text(readback),
                    "verify_ok": norm_text(readback) == norm_text(new_text),
                }
            )
            append_jsonl(self.log_root / "applied.jsonl", audit)
        if not audit["verify_ok"]:
            raise ReviewError(f"写入后校验不一致：{rel}")
        return {
            "ok": True,
            "decision": "通过",
            "status": "已写入",
            "file": rel,
            "next": next_review(summary, index),
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
                next_page = next_review(summary, item_index)
            except Exception:
                next_page = None
            self.queue(payload, "已入队", {"package_dir": package_dir, "proposed": proposed})
            return {"ok": True, "decision": "指正", "status": "已入队", "next": next_page}
        return self.apply_content(payload)


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
        try:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ReviewError("请求格式错误")
            result = self.actions.handle(payload)
            self.write_json(200, result)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
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
    parser.add_argument("--obq", default=str(DEFAULT_OBQ), type=Path, help="Path to the queued Obsidian CLI wrapper.")
    parser.add_argument("--probe-note", default=DEFAULT_PROBE_NOTE, help="Note to read before applying approved content.")
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
