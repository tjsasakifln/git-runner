#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("builder", HERE / "build_n8n_json_zip.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Não foi possível carregar o builder principal")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

EXACT_REPO = "https://github.com/Siphon880gh/n8n-automation-viewer.git"
EXACT_COMMIT = "d02d9df818474098065d953dfcbb17840d776eb8"
PUBLIC_REPO = "nusquama/n8nworkflows.xyz"
PUBLIC_REF = "main"
API_ROOT = f"https://api.github.com/repos/{PUBLIC_REPO}/git/trees"
_public_tree: dict[str, dict[str, Any]] | None = None

STOP = {
    "a","an","the","and","or","to","of","in","on","for","with","using","use","how",
    "build","built","make","made","create","created","free","template","workflow","workflows",
    "this","that","your","my","i","is","are","can","from","by","no","code","ultimate",
    "tutorial","watch","me","get","system","step","guide","new","everything"
}
ALIASES = {
    "fb":"facebook","x":"twitter","tweets":"twitter","tweet":"twitter",
    "sheets":"sheet","maps":"map","videos":"video","images":"image","documents":"document",
    "emails":"email","agents":"agent","assistants":"assistant","shorts":"short","reels":"reel",
    "posts":"post","invoices":"invoice","customers":"customer","leads":"lead",
    "scraping":"scrape","scraper":"scrape","scrapes":"scrape","scraped":"scrape",
    "automation":"automate","automated":"automate","automates":"automate","automating":"automate",
    "analysis":"analyze","analyzer":"analyze","analyse":"analyze","analyzes":"analyze","analyzing":"analyze",
    "creation":"create","creator":"create","generating":"generate","generator":"generate","generated":"generate",
    "tracking":"track","tracker":"track","qualifier":"qualify","qualifies":"qualify","qualification":"qualify",
    "personalized":"personalize","personalization":"personalize","manager":"manage","management":"manage",
}


def request_bytes(url: str, *, api: bool = False, timeout: int = 180) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json" if api else "application/octet-stream",
        "User-Agent": "n8n-json-pack-builder/2.0",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if api and token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def normalize_tokens(text: str) -> list[str]:
    text = builder.safe_name(text or "", 500).lower().replace("_", " ").replace("-", " ")
    raw = re.findall(r"[a-z0-9]+", text)
    result: list[str] = []
    for token in raw:
        token = ALIASES.get(token, token)
        if token in STOP or len(token) < 2:
            continue
        if len(token) > 5 and token.endswith("s") and token not in {"sales", "maps"}:
            token = token[:-1]
        result.append(token)
    return result


def skipped_direct_downloads(data: dict[str, Any]):
    manifest: list[dict[str, Any]] = []
    for row in data["rows"]:
        for position, url in enumerate(row.get("template_urls") or [], start=1):
            manifest.append({
                "excel_row": row["excel_row"],
                "position": position,
                "name": row.get("name") or row.get("title") or "workflow",
                "title": row.get("title") or "",
                "creator": row.get("creator") or "",
                "youtube_id": row.get("id") or "",
                "original_drive_url": url,
                "drive_id": builder.extract_drive_id(url) or "desconhecido",
                "status": "pendente_fallback",
                "source": "",
                "match_score": "",
                "json_file": "",
                "validation": "",
                "error": "Google Drive original indisponível na tentativa direta (0 de 311 arquivos públicos)",
            })
    print(f"Passagem direta já auditada; {len(manifest)} links seguem para recuperação.", flush=True)
    return manifest, {}


def split_workflows(payload: bytes) -> list[bytes]:
    valid, obj = builder.valid_json_bytes(payload)
    if not valid:
        return []
    if builder.is_n8n_workflow(obj) and isinstance(obj, dict):
        return [payload]
    if isinstance(obj, list):
        workflows = [item for item in obj if isinstance(item, dict) and builder.is_n8n_workflow(item)]
        return [json.dumps(item, ensure_ascii=False, indent=2).encode("utf-8") for item in workflows]
    return []


def recover_exact_archive(data: dict[str, Any], manifest: list[dict[str, Any]]) -> int:
    target = builder.WORK / "exact-archive"
    shutil.rmtree(target, ignore_errors=True)
    subprocess.run(
        ["git", "clone", "--depth=1", EXACT_REPO, str(target)],
        check=True, timeout=900,
    )
    subprocess.run(["git", "-C", str(target), "checkout", EXACT_COMMIT], check=True, timeout=180)

    json_root = target / "oleg-browser" / "json"
    if not json_root.exists():
        raise RuntimeError("Diretório de JSONs exatos não encontrado")
    all_files = list(json_root.rglob("*.json"))
    by_stem: dict[str, list[Path]] = {}
    for path in all_files:
        stem = path.stem
        by_stem.setdefault(stem, []).append(path)
        # Permite nomes com sufixos, como VIDEOID-2.json.
        base = re.split(r"[-_.]", stem, maxsplit=1)[0]
        by_stem.setdefault(base, []).append(path)

    pending_by_row: dict[int, list[dict[str, Any]]] = {}
    for item in manifest:
        if item["status"] == "pendente_fallback":
            pending_by_row.setdefault(int(item["excel_row"]), []).append(item)

    recovered = 0
    for row in data["rows"]:
        row_no = int(row["excel_row"])
        entries = sorted(pending_by_row.get(row_no, []), key=lambda x: int(x["position"]))
        if not entries:
            continue
        video_id = row.get("id") or ""
        candidates = []
        seen: set[Path] = set()
        for key in (video_id,):
            for path in by_stem.get(key, []):
                if path not in seen:
                    candidates.append(path)
                    seen.add(path)
        if not candidates:
            candidates = [p for p in all_files if video_id and video_id in p.name]

        pieces: list[tuple[bytes, Path, int]] = []
        for path in candidates:
            try:
                for index, payload in enumerate(split_workflows(path.read_bytes()), start=1):
                    pieces.append((payload, path, index))
            except Exception:
                continue

        for entry, (payload, source_path, part_index) in zip(entries, pieces):
            base = builder.safe_name(row.get("name") or row.get("title") or video_id, 58)
            suffix = f"_{part_index:02d}" if len(pieces) > 1 else ""
            destination = builder.JSON_DIR / f"{row_no:03d}_{entry['position']:02d}_{base}_{video_id}{suffix}.json"
            destination.write_bytes(payload)
            _, obj = builder.valid_json_bytes(payload)
            entry["status"] = "recuperado_arquivo_exato"
            entry["source"] = f"https://github.com/Siphon880gh/n8n-automation-viewer/blob/{EXACT_COMMIT}/{source_path.relative_to(target).as_posix()}"
            entry["match_score"] = 100
            entry["json_file"] = str(destination.relative_to(builder.OUT))
            entry["validation"] = "workflow_n8n_validado" if builder.is_n8n_workflow(obj) else "json_valido_estrutura_nao_confirmada"
            entry["error"] += " | recuperado por correspondência exata do ID do vídeo"
            recovered += 1

    print(f"Arquivo exato: {recovered} JSONs recuperados por ID de vídeo entre {len(all_files)} arquivos disponíveis.", flush=True)
    return recovered


def load_public_tree() -> dict[str, dict[str, Any]]:
    global _public_tree
    if _public_tree is not None:
        return _public_tree
    root = json.loads(request_bytes(f"{API_ROOT}/{PUBLIC_REF}", api=True, timeout=180))
    workflows = next((item for item in root.get("tree", []) if item.get("path") == "workflows" and item.get("type") == "tree"), None)
    if workflows is None:
        raise RuntimeError("Diretório workflows não encontrado no arquivo público")
    payload = json.loads(request_bytes(f"{API_ROOT}/{workflows['sha']}", api=True, timeout=300))
    if payload.get("truncated"):
        raise RuntimeError("A listagem direta de workflows foi truncada")
    folders = {
        item["path"]: {"sha": item["sha"]}
        for item in payload.get("tree", [])
        if item.get("type") == "tree" and item.get("path")
    }
    print(f"Índice público secundário: {len(folders)} pastas.", flush=True)
    _public_tree = folders
    return folders


def fetch_public_json(folder: str) -> tuple[bytes | None, str | None]:
    info = load_public_tree().get(folder)
    if not info:
        return None, "pasta não encontrada"
    try:
        listing = json.loads(request_bytes(f"{API_ROOT}/{info['sha']}", api=True, timeout=120))
        candidates = [
            item for item in listing.get("tree", [])
            if item.get("type") == "blob"
            and str(item.get("path", "")).lower().endswith(".json")
            and "metada" not in str(item.get("path", "")).lower()
            and "metadata" not in str(item.get("path", "")).lower()
        ]
        if not candidates:
            return None, "nenhum JSON de workflow na pasta"
        item = max(candidates, key=lambda x: int(x.get("size") or 0))
        path = f"workflows/{folder}/{item['path']}"
        url = f"https://raw.githubusercontent.com/{PUBLIC_REPO}/{PUBLIC_REF}/{urllib.parse.quote(path, safe='/')}"
        return request_bytes(url, timeout=120), str(item["path"])
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def semantic_recovery(data: dict[str, Any], manifest: list[dict[str, Any]]) -> int:
    pending = [item for item in manifest if item["status"] == "pendente_fallback"]
    if not pending:
        return 0
    folders = list(load_public_tree())
    folder_tokens = {folder: set(normalize_tokens(re.sub(r"-\d+$", "", folder))) for folder in folders}
    df = Counter(token for tokens in folder_tokens.values() for token in tokens)
    total = len(folders)
    inverted: dict[str, set[str]] = {}
    for folder, tokens in folder_tokens.items():
        for token in tokens:
            inverted.setdefault(token, set()).add(folder)

    rows_by_no = {int(row["excel_row"]): row for row in data["rows"]}
    pending_by_row: dict[int, list[dict[str, Any]]] = {}
    for item in pending:
        pending_by_row.setdefault(int(item["excel_row"]), []).append(item)

    used: set[str] = set()
    recovered = 0
    for row_no, entries in sorted(pending_by_row.items()):
        row = rows_by_no[row_no]
        name_tokens = normalize_tokens(row.get("name") or "")
        title_tokens = normalize_tokens(row.get("title") or "")
        query_tokens = set(name_tokens + title_tokens)
        candidates: set[str] = set()
        for token in query_tokens:
            candidates.update(inverted.get(token, ()))

        def idf(token: str) -> float:
            return math.log((total + 1) / (df.get(token, 0) + 1)) + 1

        name_weight = sum(idf(t) for t in set(name_tokens)) or 1.0
        query_weight = sum(idf(t) for t in query_tokens) or 1.0

        scored: list[tuple[float, str]] = []
        for folder in candidates:
            if folder in used:
                continue
            ft = folder_tokens[folder]
            common_name = set(name_tokens) & ft
            common_query = query_tokens & ft
            if not common_query:
                continue
            name_cov = sum(idf(t) for t in common_name) / name_weight
            query_cov = sum(idf(t) for t in common_query) / query_weight
            precision = sum(idf(t) for t in common_query) / (sum(idf(t) for t in ft) or 1.0)
            exact_phrase = 1.0 if " ".join(name_tokens) and " ".join(name_tokens) in " ".join(normalize_tokens(folder)) else 0.0
            rare = max((idf(t) for t in common_query), default=0.0) / 10.0
            score = min(100.0, 58 * name_cov + 22 * query_cov + 10 * precision + 8 * exact_phrase + 2 * min(1.0, rare))
            common_count = len(common_query)
            if (len(set(name_tokens)) >= 3 and common_count < 2 and name_cov < 0.75) or (len(set(name_tokens)) < 3 and name_cov < 0.55):
                continue
            scored.append((score, folder))
        scored.sort(reverse=True)
        acceptable = [(score, folder) for score, folder in scored if score >= 48]

        for entry, (score, folder) in zip(sorted(entries, key=lambda x: int(x["position"])), acceptable):
            payload, filename = fetch_public_json(folder)
            if not payload:
                continue
            pieces = split_workflows(payload)
            if not pieces:
                continue
            destination = builder.JSON_DIR / f"{row_no:03d}_{entry['position']:02d}_{builder.safe_name(row.get('name') or folder, 58)}_public_{re.search(r'(\d+)$', folder).group(1) if re.search(r'(\d+)$', folder) else 'semid'}.json"
            destination.write_bytes(pieces[0])
            _, obj = builder.valid_json_bytes(pieces[0])
            entry["status"] = "recuperado_espelho_publico"
            entry["source"] = f"https://github.com/{PUBLIC_REPO}/tree/{PUBLIC_REF}/workflows/{folder}"
            entry["match_score"] = round(score, 2)
            entry["json_file"] = str(destination.relative_to(builder.OUT))
            entry["validation"] = "workflow_n8n_validado" if builder.is_n8n_workflow(obj) else "json_valido_estrutura_nao_confirmada"
            entry["error"] += f" | correspondência semântica auditável; arquivo {filename}"
            used.add(folder)
            recovered += 1

        for entry in entries:
            if entry["status"] == "pendente_fallback":
                preview = "; ".join(f"{score:.1f}:{folder}" for score, folder in scored[:3])
                entry["status"] = "indisponivel"
                entry["error"] += f" | nenhuma cópia pública segura; melhores candidatos: {preview}"

    print(f"Arquivo secundário: {recovered} JSONs adicionais recuperados.", flush=True)
    return recovered


def combined_recovery(data: dict[str, Any], manifest: list[dict[str, Any]]) -> None:
    recover_exact_archive(data, manifest)
    semantic_recovery(data, manifest)


builder.direct_downloads = skipped_direct_downloads
builder.apply_fallback = combined_recovery

if __name__ == "__main__":
    raise SystemExit(builder.main())
