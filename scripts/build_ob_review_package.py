#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


STRUCTURE_KINDS = {
    "rename": "改名",
    "move": "移动",
    "merge": "合并",
    "split": "拆分",
    "folder": "重组",
}


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def slug(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\s]+", "-", value.strip()).strip("-")
    return value[:80] or "note"


def read_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise SystemExit("Manifest must be a JSON object.")
    items = data.get("items")
    if not isinstance(items, list):
        raise SystemExit("Manifest must contain an items array.")
    return data


def line_delta(old: str, new: str) -> tuple[int, int, int]:
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed, added + removed


def diff_rows(old: str, new: str) -> str:
    rows: list[str] = []
    old_no = new_no = 0
    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines())
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for left, right in zip(old.splitlines()[i1:i2], new.splitlines()[j1:j2]):
                old_no += 1
                new_no += 1
                rows.append(
                    f'<div class="drow ctx"><span>{old_no}</span><span>{new_no}</span><pre>{esc(left)}</pre></div>'
                )
            continue
        if tag in ("replace", "delete"):
            for left in old.splitlines()[i1:i2]:
                old_no += 1
                rows.append(f'<div class="drow del"><span>{old_no}</span><span></span><pre>{esc(left)}</pre></div>')
        if tag in ("replace", "insert"):
            for right in new.splitlines()[j1:j2]:
                new_no += 1
                rows.append(f'<div class="drow add"><span></span><span>{new_no}</span><pre>{esc(right)}</pre></div>')
    return "\n".join(rows) or '<div class="empty">无差异</div>'


def numbered_source(text: str) -> str:
    lines = text.splitlines() or [""]
    out = []
    for index, line in enumerate(lines, 1):
        out.append(f'<div class="srow"><span>{index}</span><pre>{esc(line)}</pre></div>')
    return "\n".join(out)


def render_markdown_minimal(text: str) -> str:
    parts: list[str] = []
    in_list = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            if in_list:
                parts.append("</ul>")
                in_list = False
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            if in_list:
                parts.append("</ul>")
                in_list = False
            level = len(heading.group(1))
            parts.append(f"<h{level}>{esc(heading.group(2))}</h{level}>")
            continue
        item = re.match(r"^\s*[-*]\s+(.+)$", line)
        if item:
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{esc(item.group(1))}</li>")
            continue
        if in_list:
            parts.append("</ul>")
            in_list = False
        parts.append(f"<p>{esc(line)}</p>")
    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def normalize_structure(item: dict[str, Any]) -> list[dict[str, str]]:
    raw = item.get("structure") or item.get("structure_changes") or []
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for change in raw:
        if not isinstance(change, dict):
            continue
        kind = str(change.get("kind") or "").strip().lower()
        if kind not in STRUCTURE_KINDS:
            continue
        old_path = str(change.get("old_path") or item.get("path") or "").replace("\\", "/").strip("/")
        new_path = str(change.get("new_path") or change.get("target_path") or "").replace("\\", "/").strip("/")
        result.append(
            {
                "kind": kind,
                "kind_label": STRUCTURE_KINDS[kind],
                "old_path": old_path,
                "new_path": new_path,
                "reason": str(change.get("reason") or "").strip(),
            }
        )
    return result


def structure_count_text(changes: list[dict[str, str]]) -> str:
    counts = Counter(change["kind_label"] for change in changes)
    total = sum(counts.values())
    if not total:
        return ""
    parts = [f"{label}{counts[label]}" for label in STRUCTURE_KINDS.values() if counts[label]]
    return f"□{total}" + (f" · {' · '.join(parts)}" if parts else "")


def link_badge(item: dict[str, Any]) -> str:
    links = item.get("links") or {}
    if not isinstance(links, dict):
        return ""
    added = len(links.get("added") or [])
    removed = len(links.get("removed") or [])
    suggested = len(links.get("suggested") or [])
    if not (added or removed or suggested):
        return ""
    text = f"链 +{added}/-{removed}" + (f" 建{suggested}" if suggested else "")
    return f'<span class="badge link">{esc(text)}</span>'


def write_review_page(out_dir: Path, index: int, item: dict[str, Any], structure: list[dict[str, str]]) -> str:
    path = str(item.get("path") or f"note-{index}.md").replace("\\", "/").strip("/")
    old_text = str(item.get("old_text") or "")
    new_text = str(item.get("new_text") or "")
    added, removed, total = line_delta(old_text, new_text)
    filename = f"{index:03d}-{slug(Path(path).stem)}.html"
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(path)}</title>
<style>
html,body{{margin:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:#1f2937;background:#f6f7f9}}
body{{display:grid;grid-template-rows:auto minmax(0,1fr)}}
.bar{{display:grid;grid-template-columns:minmax(0,1fr) minmax(172px,46vw) 30px;gap:5px;align-items:center;padding:5px 7px;background:#fff;border-bottom:1px solid #d9dee7}}
.path{{font-size:12px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.review{{display:grid;grid-template-columns:minmax(0,1fr) 28px auto;gap:4px;align-items:center}}
.review input{{height:26px;border:1px solid #d9dee7;border-radius:6px;padding:2px 6px;font-size:12px}}
.btn{{width:28px;height:26px;border:1px solid #d9dee7;border-radius:6px;background:#fff;cursor:pointer}}
.btn.saved{{background:#eaf7ef;border-color:#86d19a;color:#0f5132}}
.btn:disabled{{opacity:.58;cursor:wait}}
.state{{font-size:11px;color:#667085;min-width:52px;white-space:nowrap}}
.state[data-tone="error"]{{color:#b42318;font-weight:700}}
body[data-decision="通过"] .bar{{border-left:4px solid #138a4b}}
body[data-decision="指正"] .bar{{border-left:4px solid #a15c00}}
.main{{overflow:auto;padding:7px;display:grid;gap:7px}}
.panel{{background:#fff;border:1px solid #d9dee7;border-radius:7px;overflow:hidden}}
.head{{display:flex;align-items:center;gap:6px;padding:5px 7px;border-bottom:1px solid #eef2f7;font-size:12px;font-weight:700}}
.body{{overflow:auto}}
.diff{{max-height:min(52vh,620px)}}
.drow{{display:grid;grid-template-columns:42px 42px minmax(0,1fr);font-size:12px;line-height:1.35;border-bottom:1px solid rgba(217,222,231,.55)}}
.drow span{{padding:2px 4px;text-align:right;color:#667085;background:#f8fafc;border-right:1px solid #eef2f7;font-variant-numeric:tabular-nums}}
pre{{margin:0;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}
.drow pre{{padding:2px 6px}}
.del{{background:#ffebe9}} .add{{background:#dafbe1}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:7px}}
.source{{max-height:34vh}}
.srow{{display:grid;grid-template-columns:42px minmax(0,1fr);font-size:12px;line-height:1.35;border-bottom:1px solid rgba(217,222,231,.55)}}
.srow span{{padding:2px 4px;text-align:right;color:#667085;background:#f8fafc;border-right:1px solid #eef2f7;font-variant-numeric:tabular-nums}}
.srow pre{{padding:2px 6px}}
.rendered{{padding:8px;font-size:13px;line-height:1.55;display:none}}
.rendered h1,.rendered h2,.rendered h3{{margin:.3em 0}} .rendered p{{margin:.35em 0}}
.mode-render .source{{display:none}} .mode-render .rendered{{display:block}}
.structure-note{{background:#fff8e1;border:1px solid #f1d58a;border-radius:7px;padding:6px;font-size:12px}}
@media(max-width:720px){{.bar{{grid-template-columns:minmax(0,1fr) minmax(168px,56vw) 28px}}.state{{min-width:46px;font-size:10px}}.main{{padding:5px;gap:5px}}.two{{grid-template-columns:1fr}}.source{{max-height:28vh}}.diff{{max-height:48vh}}}}
</style>
</head>
<body>
<header class="bar">
  <div class="path">{esc(path)} <span title="content line delta">Δ{total}</span></div>
  <div class="review"><input placeholder="指正" data-note><button class="btn" title="空白通过；有内容指正" data-submit>✎</button><span class="state" data-state></span></div>
  <button class="btn" title="源码/渲染" data-mode>◐</button>
</header>
<main class="main">
  {"<section class='structure-note'>此文件有结构变动，请在总入口点 □ 审核文件名/路径变化；本页只用于文件内内容。</section>" if structure else ""}
  <section class="panel"><div class="head">差异 <span>+{added}/-{removed}</span></div><div class="body diff">{diff_rows(old_text, new_text)}</div></section>
  <section class="two">
    <div class="panel"><div class="head">旧稿</div><div class="body source">{numbered_source(old_text)}</div><div class="rendered">{render_markdown_minimal(old_text)}</div></div>
    <div class="panel"><div class="head">新稿</div><div class="body source">{numbered_source(new_text)}</div><div class="rendered">{render_markdown_minimal(new_text)}</div></div>
  </section>
</main>
<script>
const key = "ob-review:" + {json.dumps(path, ensure_ascii=False)};
const REVIEW_PATH = {json.dumps(path, ensure_ascii=False)};
let saved = {{}};
try {{
  saved = JSON.parse(localStorage.getItem(key) || "{{}}") || {{}};
  if (saved && saved.ok === false) {{
    localStorage.removeItem(key);
    saved = {{}};
  }}
}} catch (_) {{
  saved = {{}};
}}
const note = document.querySelector("[data-note]");
const state = document.querySelector("[data-state]");
if (saved.note) note.value = saved.note;
function flash(button) {{
  if (!button) return;
  const old = button.textContent || "✎";
  button.textContent = "✓";
  button.classList.add("saved");
  window.setTimeout(() => {{
    button.textContent = old;
    button.classList.remove("saved");
  }}, 900);
}}
function applyReview(data, button) {{
  document.body.dataset.decision = data.decision || "";
  if (state) {{
    state.textContent = data.decision ? (data.status || "已存") + " · " + data.decision : "";
    state.dataset.tone = data.ok === false ? "error" : "";
  }}
  flash(button);
}}
if (saved.decision) applyReview(saved, null);
document.querySelector("[data-submit]").addEventListener("click", async event => {{
  const value = note.value.trim();
  const button = event.currentTarget;
  const data = {{decision: value ? "指正" : "通过", note: value, file: REVIEW_PATH, time: new Date().toISOString()}};
  localStorage.setItem(key, JSON.stringify(data));
  if (state) {{ state.textContent = "处理中..."; state.dataset.tone = ""; }}
  button.disabled = true;
  try {{
    const response = await fetch("/api/review", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        scope: "content",
        file: REVIEW_PATH,
        decision: data.decision,
        note: data.note,
        page: window.location.pathname,
        href: window.location.href,
        time: new Date().toISOString()
      }})
    }});
    const result = await response.json().catch(() => ({{}}));
    if (!response.ok || !result.ok) throw new Error(result.error || ("HTTP " + response.status));
    const saved = Object.assign({{}}, data, result, {{time:new Date().toISOString()}});
    localStorage.setItem(key, JSON.stringify(saved));
    applyReview(saved, button);
    if (result.next) window.setTimeout(() => {{ window.location.href = result.next; }}, 450);
  }} catch (error) {{
    localStorage.removeItem(key);
    document.body.dataset.decision = "";
    if (state) {{ state.textContent = "失败"; state.dataset.tone = "error"; }}
  }} finally {{
    button.disabled = false;
  }}
}});
document.querySelector("[data-mode]").addEventListener("click", () => document.body.classList.toggle("mode-render"));
</script>
</body>
</html>
"""
    (out_dir / filename).write_text(page, encoding="utf-8")
    return filename


def proposed_filename(index: int, path: str) -> str:
    return f"{index:03d}-{slug(Path(path).stem)}.md"


def build_tree(items: list[dict[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {"dirs": {}, "files": [], "changed": 0, "count": 0, "structure": 0}
    for item in items:
        path = str(item["path"]).replace("\\", "/").strip("/")
        parts = [part for part in path.split("/") if part]
        node = root
        node["count"] += 1
        node["changed"] += int(item["changed"])
        node["structure"] += int(item["structure_count"])
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": [], "changed": 0, "count": 0, "structure": 0})
            node["count"] += 1
            node["changed"] += int(item["changed"])
            node["structure"] += int(item["structure_count"])
        node["files"].append(item)
    return root


def render_tree(node: dict[str, Any], name: str = "", base: str = "") -> str:
    chunks: list[str] = []
    for dirname in sorted(node["dirs"]):
        child = node["dirs"][dirname]
        path = f"{base}/{dirname}" if base else dirname
        label = f"{child['changed']}/{child['count']}" + (f" · □{child['structure']}" if child["structure"] else "")
        chunks.append(
            f'<details class="dir" open><summary><span>{esc(dirname)}</span><b>{esc(label)}</b></summary><div>{render_tree(child, dirname, path)}</div></details>'
        )
    for item in sorted(node["files"], key=lambda x: x["path"].lower()):
        badges = []
        if item["structure_count"]:
            badges.append(f'<button class="sq" data-structure-path="{esc(item["path"])}">□{item["structure_count"]}</button>')
        if item["delta"]:
            badges.append(f'<span class="badge">Δ{item["delta"]}</span>')
        badges.append(link_badge(item["raw"]))
        chunks.append(
            f'<div class="file" data-path="{esc(item["path"])}" data-src="{esc(item["src"])}" data-structure="{str(bool(item["structure_count"])).lower()}">'
            f'<button class="file-main"><span>{esc(Path(item["path"]).name)}</span></button><span class="badges">{"".join(badges)}</span></div>'
        )
    return "".join(chunks)


def write_index(out_dir: Path, manifest: dict[str, Any], prepared: list[dict[str, Any]]) -> None:
    title = str(manifest.get("title") or "OB Review")
    vault_label = str(manifest.get("vault_label") or "note")
    structure_map = {
        item["path"]: {"path": item["path"], "count": structure_count_text(item["structure"]), "items": item["structure"]}
        for item in prepared
        if item["structure"]
    }
    structure_json = json.dumps(structure_map, ensure_ascii=False).replace("<", "\\u003c")
    total = len(prepared)
    changed = sum(1 for item in prepared if item["changed"])
    structure_total = sum(item["structure_count"] for item in prepared)
    first_src = next((item["src"] for item in prepared if item["changed"]), prepared[0]["src"] if prepared else "")
    tree = render_tree(build_tree(prepared))
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
html,body{{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:#1f2937;background:#f6f7f9;overflow:hidden}}
.top{{height:34px;display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:6px;align-items:center;padding:4px 8px;background:#fff;border-bottom:1px solid #d9dee7;box-sizing:border-box}}
.btn,.sq{{height:25px;border:1px solid #d9dee7;border-radius:6px;background:#fff;cursor:pointer}}
.btn.saved{{background:#eaf7ef;border-color:#86d19a;color:#0f5132}}
.btn:disabled{{opacity:.58;cursor:wait}}
.brand{{font-size:13px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.stat{{font-size:11px;color:#667085;border:1px solid #d9dee7;border-radius:999px;padding:2px 6px;background:#fbfcfe;white-space:nowrap}}
iframe{{width:100%;height:calc(100vh - 34px);border:0;background:#fff}}
.overlay[hidden],.drawer[hidden]{{display:none}}
.overlay{{position:fixed;inset:0;background:rgba(15,23,42,.25);z-index:10}}
.panel{{width:min(92vw,820px);height:calc(100vh - 18px);margin:9px;display:grid;grid-template-rows:auto auto minmax(0,1fr);background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 18px 50px rgba(15,23,42,.24)}}
.panel-head,.tools{{display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:6px;align-items:center;padding:6px;border-bottom:1px solid #d9dee7}}
.tools{{grid-template-columns:minmax(0,1fr) auto auto}}
input{{height:26px;border:1px solid #d9dee7;border-radius:6px;padding:2px 7px;font-size:12px}}
.tree{{overflow:auto;padding:7px;font-size:12px}}
.dir summary{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;padding:4px;border-radius:6px;cursor:pointer}}
.dir summary:hover,.file:hover{{background:#eef2f7}}
.dir>div{{margin-left:13px;padding-left:6px;border-left:1px solid #d9dee7}}
.file{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px;align-items:center;border-radius:6px}}
.file-main{{width:100%;border:0;background:transparent;text-align:left;padding:5px;font-size:12px;cursor:pointer;overflow:hidden}}
.file-main span{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.badges{{display:flex;gap:3px;align-items:center}}
.badge,.sq{{font-size:11px;line-height:1;white-space:nowrap}}
.sq{{border-color:#8bbcff;background:#edf4ff;color:#0a3069;font-weight:800;padding:0 7px;border-radius:999px}}
.badge{{display:inline-flex;border:1px solid #bfd7ff;background:#edf4ff;color:#0a3069;border-radius:999px;padding:3px 5px}}
.drawer{{position:fixed;right:10px;top:10px;z-index:20;width:min(460px,calc(100vw - 40px));max-height:calc(100vh - 20px);display:grid;grid-template-rows:auto minmax(0,1fr);background:#fff;border:1px solid #cfd6e1;border-radius:8px;overflow:hidden;box-shadow:0 18px 50px rgba(15,23,42,.24)}}
.drawer-head{{display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:6px;align-items:center;padding:7px;border-bottom:1px solid #d9dee7}}
.drawer-title{{font-size:13px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.drawer-body{{overflow:auto;padding:8px;display:grid;gap:6px}}
.card{{display:grid;gap:4px;border:1px solid #d9e8ff;border-radius:7px;background:#f8fbff;padding:6px}}
.card[data-decision="通过"]{{border-left:4px solid #138a4b}}
.card[data-decision="指正"]{{border-left:4px solid #a15c00}}
.kind{{justify-self:start;border:1px solid #bfd7ff;background:#edf4ff;color:#0a3069;border-radius:999px;padding:2px 7px;font-size:11px;font-weight:800}}
.paths{{display:grid;gap:2px;font-size:11px;color:#344054}}
.paths div{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.reason{{font-size:11px;color:#667085}}
.review{{display:grid;grid-template-columns:minmax(0,1fr) 28px auto;gap:4px;align-items:center}}
.state{{font-size:11px;color:#667085;min-width:52px;white-space:nowrap}}
.state[data-tone="error"]{{color:#b42318;font-weight:700}}
@media(max-width:720px){{.panel{{width:100vw;height:100vh;margin:0;border-radius:0}}.drawer{{top:auto;left:0;right:0;bottom:0;width:auto;max-height:58vh;border-radius:8px 8px 0 0}}.badges .badge:not(:first-child){{display:none}}}}
</style>
</head>
<body>
<header class="top"><button class="btn" data-open>☰</button><div class="brand">{esc(title)}</div><div class="stat">{changed}/{total} · □{structure_total}</div></header>
<iframe class="viewer" src="{esc(first_src)}"></iframe>
<div class="overlay" data-overlay hidden>
  <section class="panel">
    <div class="panel-head"><button class="btn" data-close>×</button><b>{esc(vault_label)}</b><button class="btn" data-expand>▾</button></div>
    <div class="tools"><input data-search placeholder="筛选路径"><button class="sq" data-structure-filter>□{structure_total}</button><span class="stat">{changed}/{total}</span></div>
    <nav class="tree">{tree}</nav>
  </section>
  <aside class="drawer" data-drawer hidden>
    <div class="drawer-head"><button class="btn" data-drawer-close>×</button><div class="drawer-title" data-drawer-title>结构变动</div><span class="stat" data-drawer-count></span></div>
    <div class="drawer-body" data-drawer-body></div>
  </aside>
</div>
<script type="application/json" id="structure-data">{structure_json}</script>
<script>
const viewer = document.querySelector(".viewer");
const overlay = document.querySelector("[data-overlay]");
const drawer = document.querySelector("[data-drawer]");
const files = Array.from(document.querySelectorAll(".file"));
const search = document.querySelector("[data-search]");
const data = JSON.parse(document.getElementById("structure-data").textContent || "{{}}");
let structureOnly = false;
function esc(s){{return String(s??"").replace(/[&<>"']/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));}}
function closeDrawer(){{drawer.hidden=true;}}
function showStructure(path){{
  const entry = data[path];
  if(!entry) return;
  document.querySelector("[data-drawer-title]").textContent = entry.path || path;
  document.querySelector("[data-drawer-count]").textContent = entry.count || "";
  document.querySelector("[data-drawer-body]").innerHTML = entry.items.map((item,i)=>{{
    const key = "ob-review-structure:" + path + "::" + item.kind + "::" + item.old_path + "::" + item.new_path;
    let saved = {{}};
    try{{saved = JSON.parse(localStorage.getItem(key)||"{{}}")||{{}}; if(saved&&saved.ok===false){{localStorage.removeItem(key);saved={{}};}}}}catch(_ ){{saved={{}};}}
    return `<div class="card" data-key="${{esc(key)}}" data-path="${{esc(path)}}" data-kind="${{esc(item.kind||"")}}" data-old-path="${{esc(item.old_path||"")}}" data-new-path="${{esc(item.new_path||"")}}" data-decision="${{esc(saved.decision||"")}}">
      <span class="kind">□ ${{esc(item.kind_label)}}</span>
      <div class="paths"><div title="${{esc(item.old_path)}}">旧：${{esc(item.old_path || "-")}}</div><div title="${{esc(item.new_path)}}">新：${{esc(item.new_path || "-")}}</div></div>
      ${{item.reason ? `<div class="reason">${{esc(item.reason)}}</div>` : ""}}
      <div class="review"><input value="${{esc(saved.note||"")}}" placeholder="指正" data-note><button class="btn" data-submit>✎</button><span class="state" data-state>${{saved.decision ? "已存 · " + esc(saved.decision) : ""}}</span></div>
    </div>`;
  }}).join("");
  drawer.hidden = false;
}}
function refresh(){{
  const q = (search.value||"").toLowerCase().trim();
  files.forEach(row=>{{
    const path = (row.dataset.path||"").toLowerCase();
    const okText = !q || path.includes(q);
    const okStructure = !structureOnly || row.dataset.structure === "true";
    row.hidden = !(okText && okStructure);
  }});
}}
document.querySelector("[data-open]").onclick=()=>{{overlay.hidden=false;setTimeout(()=>search.focus(),0);}};
document.querySelector("[data-close]").onclick=()=>{{closeDrawer();overlay.hidden=true;}};
document.querySelector("[data-drawer-close]").onclick=closeDrawer;
document.querySelector("[data-expand]").onclick=()=>document.querySelectorAll("details").forEach(d=>d.open=true);
document.querySelector("[data-structure-filter]").onclick=e=>{{structureOnly=!structureOnly;e.currentTarget.classList.toggle("active",structureOnly);refresh();}};
search.oninput=refresh;
files.forEach(row=>{{
  row.querySelector(".file-main").onclick=()=>{{
    if(row.dataset.structure === "true"){{showStructure(row.dataset.path); return;}}
    viewer.src=row.dataset.src; overlay.hidden=true;
  }};
  const sq = row.querySelector("[data-structure-path]");
  if(sq) sq.onclick=(ev)=>{{ev.stopPropagation();showStructure(row.dataset.path);}};
}});
document.querySelector("[data-drawer-body]").onclick=async e=>{{
  const btn=e.target.closest("[data-submit]");
  if(!btn) return;
  const card=btn.closest(".card");
  const note=card.querySelector("[data-note]").value.trim();
  const decision=note ? "指正" : "通过";
  const state=card.querySelector("[data-state]");
  const data={{
    scope:"structure",
    decision,
    note,
    file:card.dataset.path||"",
    path:card.dataset.path||"",
    kind:card.dataset.kind||"",
    old_path:card.dataset.oldPath||"",
    new_path:card.dataset.newPath||"",
    page:window.location.pathname,
    href:window.location.href,
    time:new Date().toISOString()
  }};
  localStorage.setItem(card.dataset.key, JSON.stringify(data));
  if(state){{state.textContent="处理中...";state.dataset.tone="";}}
  btn.disabled=true;
  try{{
    const response=await fetch("/api/review",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(data)}});
    const result=await response.json().catch(()=>({{}}));
    if(!response.ok||!result.ok) throw new Error(result.error||("HTTP "+response.status));
    const saved=Object.assign({{}},data,result,{{time:new Date().toISOString()}});
    localStorage.setItem(card.dataset.key, JSON.stringify(saved));
    card.dataset.decision=saved.decision||decision;
    if(state){{state.textContent=(saved.status||"已入队")+" · "+(saved.decision||decision);state.dataset.tone="";}}
    const old=btn.textContent || "✎";
    btn.textContent="✓";
    btn.classList.add("saved");
    window.setTimeout(()=>{{btn.textContent=old;btn.classList.remove("saved");}},900);
  }}catch(error){{
    localStorage.removeItem(card.dataset.key);
    card.dataset.decision="";
    if(state){{state.textContent="失败";state.dataset.tone="error";}}
  }}finally{{
    btn.disabled=false;
  }}
}};
</script>
</body>
</html>
"""
    (out_dir / "00-OB-review-index.html").write_text(page, encoding="utf-8")


def build(manifest_path: Path, out_dir: Path) -> None:
    manifest = read_manifest(manifest_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    proposed_dir = out_dir / "proposed"
    proposed_dir.mkdir(exist_ok=True)
    prepared: list[dict[str, Any]] = []
    summary_items: list[dict[str, Any]] = []
    for index, item in enumerate(manifest["items"], 1):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or f"note-{index}.md").replace("\\", "/").strip("/")
        old_text = str(item.get("old_text") or "")
        new_text = str(item.get("new_text") or "")
        structure = normalize_structure(item)
        added, removed, total = line_delta(old_text, new_text)
        src = write_review_page(out_dir, index, item, structure)
        proposed_name = proposed_filename(index, path)
        (proposed_dir / proposed_name).write_text(new_text, encoding="utf-8")
        prepared.append(
            {
                "path": path,
                "src": src,
                "raw": item,
                "changed": bool(total or structure or item.get("changes")),
                "delta": total,
                "added": added,
                "removed": removed,
                "structure": structure,
                "structure_count": len(structure),
            }
        )
        summary_items.append(
            {
                "name": Path(path).name,
                "path": path,
                "rel_path": path,
                "review_html": src,
                "proposed": f"proposed/{proposed_name}",
                "old_chars": len(old_text),
                "new_chars": len(new_text),
                "added_lines": added,
                "removed_lines": removed,
                "line_delta": total,
                "changes": item.get("changes") or [],
                "links": item.get("links") or {},
                "structure": structure,
            }
        )
    write_index(out_dir, manifest, prepared)
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "title": manifest.get("title") or "OB Review",
                "vault_label": manifest.get("vault_label") or "note",
                "folder": manifest.get("folder") or "",
                "items": summary_items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "review-manifest.normalized.json").write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a mobile-first Obsidian review package from a JSON manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    build(args.manifest, args.out)
    print(args.out / "00-OB-review-index.html")


if __name__ == "__main__":
    main()
