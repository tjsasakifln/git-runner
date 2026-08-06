#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
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
        if item.get("type") != "tree" or not folder:
            continue
        folders[folder] = [{
            "path": f"workflows/{folder}/workflow.json",
            "size": 0,
        }]

    if not folders:
        raise RuntimeError("Nenhuma pasta de workflow foi encontrada")
    print(f"Índice público carregado: {len(folders)} pastas de workflows", flush=True)
    _tree = folders
    return folders


def fast_clone_mirror() -> Path:
    load_tree()
    return HERE


def fast_mirror_folders(_mirror: Path) -> list[str]:
    return list(load_tree())


def fast_fetch_mirror_json(_mirror: Path, folder: str) -> tuple[bytes | None, str | None]:
    if folder not in load_tree():
        return None, "pasta não encontrada"
    path = f"workflows/{folder}/workflow.json"
    quoted_path = urllib.parse.quote(path, safe="/")
    url = f"https://raw.githubusercontent.com/{REPO}/{REF}/{quoted_path}"
    try:
        return request_bytes(url, timeout=180), "workflow.json"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


builder.clone_mirror = fast_clone_mirror
builder.mirror_folders = fast_mirror_folders
builder.fetch_mirror_json = fast_fetch_mirror_json

if __name__ == "__main__":
    raise SystemExit(builder.main())
