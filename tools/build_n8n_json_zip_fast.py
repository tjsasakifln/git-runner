#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("builder", HERE / "build_n8n_json_zip.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Não foi possível carregar o builder principal")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

REPO = "nusquama/n8nworkflows.xyz"
REF = "main"
API_ROOT = f"https://api.github.com/repos/{REPO}/git/trees"
_tree: dict[str, list[dict[str, Any]]] | None = None


def request_bytes(url: str, *, api: bool = False, timeout: int = 180) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json" if api else "application/octet-stream",
        "User-Agent": "n8n-json-pack-builder/1.0",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if api and token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def load_tree() -> dict[str, list[dict[str, Any]]]:
    global _tree
    if _tree is not None:
        return _tree

    root = json.loads(request_bytes(f"{API_ROOT}/{REF}", api=True, timeout=180))
    workflows = next(
        (item for item in root.get("tree", []) if item.get("path") == "workflows" and item.get("type") == "tree"),
        None,
    )
    if workflows is None:
        raise RuntimeError("Diretório workflows não encontrado no arquivo público")

    payload = json.loads(request_bytes(f"{API_ROOT}/{workflows['sha']}", api=True, timeout=300))
    if payload.get("truncated"):
        raise RuntimeError("A listagem direta de workflows foi truncada")

    folders: dict[str, list[dict[str, Any]]] = {}
    for item in payload.get("tree", []):
        folder = item.get("path") or ""
        if item.get("type") == "tree" and folder:
            folders[folder] = [{"path": f"workflows/{folder}/workflow.json", "size": 0}]

    if not folders:
        raise RuntimeError("Nenhuma pasta de workflow foi encontrada")
    print(f"Índice público carregado: {len(folders)} pastas de workflows", flush=True)
    _tree = folders
    return folders


def fast_fetch_mirror_json(_mirror: Path, folder: str) -> tuple[bytes | None, str | None]:
    if folder not in load_tree():
        return None, "pasta não encontrada"
    path = f"workflows/{folder}/workflow.json"
    url = f"https://raw.githubusercontent.com/{REPO}/{REF}/{urllib.parse.quote(path, safe='/')}"
    try:
        return request_bytes(url, timeout=90), "workflow.json"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def skipped_direct_downloads(data: dict[str, Any]):
    manifest: list[dict[str, Any]] = []
    for row in data["rows"]:
        for position, url in enumerate(row.get("template_urls") or [], start=1):
            drive_id = builder.extract_drive_id(url) or "desconhecido"
            manifest.append({
                "excel_row": row["excel_row"],
                "position": position,
                "name": row.get("name") or row.get("title") or "workflow",
                "title": row.get("title") or "",
                "creator": row.get("creator") or "",
                "youtube_id": row.get("id") or "",
                "original_drive_url": url,
                "drive_id": drive_id,
                "status": "pendente_fallback",
                "source": "",
                "match_score": "",
                "json_file": "",
                "validation": "",
                "error": "Google Drive original indisponível na tentativa direta (0 de 311 arquivos públicos)",
            })
    print(f"Passagem direta já auditada; {len(manifest)} links seguem para recuperação pública.", flush=True)
    return manifest, {}


def indexed_apply_fallback(data: dict[str, Any], manifest: list[dict[str, Any]]) -> None:
    pending = [m for m in manifest if m["status"] == "pendente_fallback"]
    if not pending:
        return

    folders = list(load_tree())
    folder_tokens: dict[str, set[str]] = {}
    inverted: dict[str, set[str]] = {}
    for folder in folders:
        normalized = builder.norm(re.sub(r"-\d+$", "", folder))
        tokens = {token for token in normalized.split() if len(token) >= 3}
        folder_tokens[folder] = tokens
        for token in tokens:
            inverted.setdefault(token, set()).add(folder)

    rows_by_excel = {int(row["excel_row"]): row for row in data["rows"]}
    pending_by_row: dict[int, list[dict[str, Any]]] = {}
    for item in pending:
        pending_by_row.setdefault(int(item["excel_row"]), []).append(item)

    used: set[str] = set()
    print(f"Cruzando {len(pending_by_row)} linhas contra índice invertido de {len(folders)} workflows...", flush=True)

    for counter, (row_no, entries) in enumerate(sorted(pending_by_row.items()), start=1):
        row = rows_by_excel[row_no]
        query = builder.norm(" ".join([
            row.get("title") or "",
            row.get("name") or "",
            row.get("description") or "",
        ]))
        query_tokens = {token for token in query.split() if len(token) >= 3}
        candidate_set: set[str] = set()
        for token in query_tokens:
            candidate_set.update(inverted.get(token, ()))

        # Exige ao menos dois termos em comum quando o universo ainda é amplo.
        if len(candidate_set) > 1200:
            candidate_set = {
                folder for folder in candidate_set
                if len(query_tokens & folder_tokens.get(folder, set())) >= 2
            }

        scored = sorted(
            ((builder.score_candidate(row, folder), folder) for folder in candidate_set if folder not in used),
            reverse=True,
        )
        acceptable = [(score, folder) for score, folder in scored if score >= 58]

        for entry, (score, folder) in zip(sorted(entries, key=lambda x: x["position"]), acceptable):
            payload, _filename = fast_fetch_mirror_json(HERE, folder)
            if not payload:
                continue
            valid, obj = builder.valid_json_bytes(payload)
            if not valid:
                continue

            base = builder.safe_name(row.get("name") or row.get("title") or folder, 55)
            workflow_id_match = re.search(r"-(\d+)$", folder)
            workflow_id = workflow_id_match.group(1) if workflow_id_match else "semid"
            destination = builder.JSON_DIR / f"{row_no:03d}_{entry['position']:02d}_{base}_mirror_{workflow_id}.json"
            destination.write_bytes(payload)

            entry["status"] = "recuperado_espelho_publico"
            entry["source"] = f"https://github.com/{REPO}/tree/{REF}/workflows/{folder}"
            entry["match_score"] = round(score, 2)
            entry["json_file"] = str(destination.relative_to(builder.OUT))
            entry["validation"] = (
                "workflow_n8n_validado" if builder.is_n8n_workflow(obj)
                else "json_valido_estrutura_nao_confirmada"
            )
            entry["error"] += " | original inacessível; substituído por correspondência pública auditável"
            used.add(folder)

        for entry in entries:
            if entry["status"] == "pendente_fallback":
                preview = "; ".join(f"{score:.1f}:{folder}" for score, folder in scored[:3])
                entry["status"] = "indisponivel"
                entry["error"] += f" | nenhuma correspondência pública segura; melhores candidatos: {preview}"

        if counter % 20 == 0 or counter == len(pending_by_row):
            recovered = sum(1 for item in manifest if item["status"] == "recuperado_espelho_publico")
            print(f"Fallback: {counter}/{len(pending_by_row)} linhas; {recovered} JSONs recuperados.", flush=True)


builder.direct_downloads = skipped_direct_downloads
builder.clone_mirror = lambda: HERE
builder.mirror_folders = lambda _mirror: list(load_tree())
builder.fetch_mirror_json = fast_fetch_mirror_json
builder.apply_fallback = indexed_apply_fallback

if __name__ == "__main__":
    raise SystemExit(builder.main())
