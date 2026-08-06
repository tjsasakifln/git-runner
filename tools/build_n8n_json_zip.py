#!/usr/bin/env python3
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "n8n_templates_input.json"
WORK = ROOT / "_work"
OUT = ROOT / "_output"
JSON_DIR = OUT / "json"
MANIFEST_JSON = OUT / "manifest.json"
MANIFEST_CSV = OUT / "manifest.csv"
README = OUT / "README.txt"
FINAL_ZIP = ROOT / "n8n-json-listados.zip"

DRIVE_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]+)|[?&]id=([A-Za-z0-9_-]+)")
JSON_EXT_RE = re.compile(r"\.json$", re.I)


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=False, timeout=timeout)


def safe_name(value: str, max_len: int = 80) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    value = re.sub(r"_+", "_", value)
    return (value or "workflow")[:max_len]


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    stop = {
        "a","an","the","and","or","to","of","in","on","for","with","using","use","how",
        "build","built","make","create","created","free","template","workflow","n8n","ai",
        "this","that","your","my","i","is","are","can","from","by","no","code","agent",
        "system","ultimate","tutorial","watch","me","get"
    }
    toks = [t for t in value.split() if t not in stop and len(t) > 1]
    return " ".join(toks)


def extract_drive_id(url: str) -> str | None:
    m = DRIVE_ID_RE.search(url or "")
    if not m:
        return None
    return m.group(1) or m.group(2)


def valid_json_bytes(data: bytes) -> tuple[bool, Any | None]:
    try:
        text = data.decode("utf-8-sig")
        obj = json.loads(text)
        return True, obj
    except Exception:
        return False, None


def is_n8n_workflow(obj: Any) -> bool:
    if isinstance(obj, dict):
        return isinstance(obj.get("nodes"), list) and (
            isinstance(obj.get("connections"), dict) or "settings" in obj or "name" in obj
        )
    if isinstance(obj, list):
        return bool(obj) and all(isinstance(x, dict) for x in obj)
    return False


def copy_valid_json(data: bytes, dest: Path) -> tuple[bool, str]:
    ok, obj = valid_json_bytes(data)
    if not ok:
        return False, "conteudo_nao_json"
    if not is_n8n_workflow(obj):
        note = "json_valido_estrutura_nao_confirmada"
    else:
        note = "workflow_n8n_validado"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True, note


def extract_json_from_download(src: Path, base_dest: Path) -> list[tuple[Path, str]]:
    results: list[tuple[Path, str]] = []
    data = src.read_bytes()
    ok, note = copy_valid_json(data, base_dest)
    if ok:
        return [(base_dest, note)]

    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as zf:
            members = [m for m in zf.infolist() if not m.is_dir() and JSON_EXT_RE.search(m.filename)]
            for i, member in enumerate(members, start=1):
                payload = zf.read(member)
                dest = base_dest.with_name(base_dest.stem + f"__zip_{i:02d}_" + safe_name(Path(member.filename).stem, 45) + ".json")
                ok2, note2 = copy_valid_json(payload, dest)
                if ok2:
                    results.append((dest, note2 + "_extraido_de_zip"))
    return results


def gdown_one(item: dict[str, Any]) -> dict[str, Any]:
    url = item["url"]
    drive_id = item["drive_id"]
    temp = WORK / "drive" / f"{drive_id}.bin"
    temp.parent.mkdir(parents=True, exist_ok=True)
    result = {**item, "download_ok": False, "error": None, "temp": str(temp)}
    try:
        import gdown  # type: ignore
        for attempt in range(1, 4):
            try:
                if temp.exists():
                    temp.unlink()
                out = gdown.download(id=drive_id, output=str(temp), quiet=True, fuzzy=True)
                if out and temp.exists() and temp.stat().st_size > 0:
                    result["download_ok"] = True
                    result["size"] = temp.stat().st_size
                    return result
            except Exception as exc:
                result["error"] = f"gdown tentativa {attempt}: {type(exc).__name__}: {exc}"
            time.sleep(attempt * 2)
    except Exception as exc:
        result["error"] = f"import gdown: {type(exc).__name__}: {exc}"
    return result


def direct_downloads(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    tasks: list[dict[str, Any]] = []
    for row in data["rows"]:
        for pos, url in enumerate(row.get("template_urls") or [], start=1):
            drive_id = extract_drive_id(url)
            tasks.append({
                "excel_row": row["excel_row"],
                "position": pos,
                "row_name": row.get("name") or row.get("title") or "workflow",
                "title": row.get("title") or "",
                "description": row.get("description") or "",
                "creator": row.get("creator") or "",
                "youtube_id": row.get("id") or "",
                "url": url,
                "drive_id": drive_id or hashlib.sha1(url.encode()).hexdigest()[:20],
            })

    results: list[dict[str, Any]] = []
    by_row: dict[int, list[dict[str, Any]]] = {}
    log(f"Tentando baixar {len(tasks)} arquivos do Google Drive...")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(gdown_one, item): item for item in tasks}
        for n, fut in enumerate(as_completed(futures), start=1):
            item = futures[fut]
            try:
                got = fut.result()
            except Exception as exc:
                got = {**item, "download_ok": False, "error": f"{type(exc).__name__}: {exc}"}
            results.append(got)
            by_row.setdefault(got["excel_row"], []).append(got)
            if n % 25 == 0 or n == len(tasks):
                ok_count = sum(1 for r in results if r.get("download_ok"))
                log(f"Drive: {n}/{len(tasks)} processados; {ok_count} downloads recebidos.")

    manifest: list[dict[str, Any]] = []
    for got in sorted(results, key=lambda x: (x["excel_row"], x["position"])):
        row_prefix = f"{got['excel_row']:03d}_{got['position']:02d}"
        base = safe_name(got["row_name"], 55)
        dest = JSON_DIR / f"{row_prefix}_{base}_{got['drive_id']}.json"
        records: list[tuple[Path, str]] = []
        if got.get("download_ok"):
            try:
                records = extract_json_from_download(Path(got["temp"]), dest)
            except Exception as exc:
                got["error"] = f"validacao/extracao: {type(exc).__name__}: {exc}"
        if records:
            got["json_paths"] = [str(p.relative_to(OUT)) for p, _ in records]
            got["validation"] = [note for _, note in records]
            for p, note in records:
                manifest.append({
                    "excel_row": got["excel_row"],
                    "position": got["position"],
                    "name": got["row_name"],
                    "title": got["title"],
                    "creator": got["creator"],
                    "youtube_id": got["youtube_id"],
                    "original_drive_url": got["url"],
                    "drive_id": got["drive_id"],
                    "status": "baixado_google_drive",
                    "source": "Google Drive original",
                    "match_score": "",
                    "json_file": str(p.relative_to(OUT)),
                    "validation": note,
                    "error": "",
                })
            got["valid_json"] = True
        else:
            got["valid_json"] = False
            manifest.append({
                "excel_row": got["excel_row"],
                "position": got["position"],
                "name": got["row_name"],
                "title": got["title"],
                "creator": got["creator"],
                "youtube_id": got["youtube_id"],
                "original_drive_url": got["url"],
                "drive_id": got["drive_id"],
                "status": "pendente_fallback",
                "source": "",
                "match_score": "",
                "json_file": "",
                "validation": "",
                "error": got.get("error") or "download ausente, bloqueado ou conteudo nao JSON",
            })
    return manifest, by_row


def clone_mirror() -> Path | None:
    mirror = WORK / "n8nworkflows"
    if mirror.exists():
        shutil.rmtree(mirror)
    commands = [
        ["git", "clone", "--depth=1", "--filter=blob:none", "--no-checkout",
         "https://github.com/nusquama/n8nworkflows.xyz.git", str(mirror)],
        ["git", "-C", str(mirror), "sparse-checkout", "init", "--no-cone"],
    ]
    try:
        for cmd in commands:
            run(cmd, timeout=900)
        return mirror
    except Exception as exc:
        log(f"Falha ao preparar espelho nusquama: {type(exc).__name__}: {exc}")
        return None


def mirror_folders(mirror: Path) -> list[str]:
    cp = subprocess.run(
        ["git", "-C", str(mirror), "ls-tree", "-d", "--name-only", "HEAD:workflows"],
        check=True, text=True, capture_output=True, timeout=300
    )
    return [line for line in cp.stdout.splitlines() if line.strip()]


def score_candidate(row: dict[str, Any], folder: str) -> float:
    folder_no_id = re.sub(r"-\d+$", "", folder)
    f = norm(folder_no_id)
    title = norm(row.get("title") or "")
    name = norm(row.get("name") or "")
    desc = norm(row.get("description") or "")
    combined = " ".join(x for x in [title, name, desc] if x)
    if not f:
        return 0.0
    if f == title or f == name:
        return 100.0
    if f in title or title in f:
        return 92.0
    if f in name or (name and name in f):
        return 88.0
    seq_title = difflib.SequenceMatcher(None, f, title).ratio() if title else 0
    seq_name = difflib.SequenceMatcher(None, f, name).ratio() if name else 0
    ft = set(f.split())
    ct = set(combined.split())
    jacc = len(ft & ct) / max(1, len(ft | ct))
    coverage = len(ft & ct) / max(1, len(ft))
    distinctive = sum(1 for t in ft if len(t) >= 5 and t in ct) / max(1, sum(1 for t in ft if len(t) >= 5))
    return 52 * max(seq_title, seq_name) + 23 * jacc + 18 * coverage + 7 * distinctive


def fetch_mirror_json(mirror: Path, folder: str) -> tuple[bytes | None, str | None]:
    try:
        cp = subprocess.run(
            ["git", "-C", str(mirror), "ls-tree", "--name-only", f"HEAD:workflows/{folder}"],
            check=True, text=True, capture_output=True, timeout=120
        )
        names = [x for x in cp.stdout.splitlines() if x.lower().endswith(".json")]
        names = [x for x in names if not re.search(r"(metada|metadata)", x, re.I)]
        if not names:
            return None, "nenhum JSON de workflow no diretório"
        candidates: list[tuple[int, bytes, str]] = []
        for filename in names:
            cp2 = subprocess.run(
                ["git", "-C", str(mirror), "show", f"HEAD:workflows/{folder}/{filename}"],
                check=True, capture_output=True, timeout=180
            )
            candidates.append((len(cp2.stdout), cp2.stdout, filename))
        _, payload, filename = max(candidates, key=lambda x: x[0])
        return payload, filename
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def apply_fallback(data: dict[str, Any], manifest: list[dict[str, Any]]) -> None:
    pending = [m for m in manifest if m["status"] == "pendente_fallback"]
    if not pending:
        return
    mirror = clone_mirror()
    if mirror is None:
        for m in pending:
            m["status"] = "indisponivel"
            m["error"] += " | fallback público indisponível"
        return
    try:
        folders = mirror_folders(mirror)
    except Exception as exc:
        for m in pending:
            m["status"] = "indisponivel"
            m["error"] += f" | falha ao listar espelho: {exc}"
        return

    rows_by_excel = {int(r["excel_row"]): r for r in data["rows"]}
    used_folders: set[str] = set()
    pending_by_row: dict[int, list[dict[str, Any]]] = {}
    for m in pending:
        pending_by_row.setdefault(int(m["excel_row"]), []).append(m)

    log(f"Aplicando fallback público a {len(pending)} links em {len(pending_by_row)} linhas...")
    for row_no, entries in sorted(pending_by_row.items()):
        row = rows_by_excel[row_no]
        scored = sorted(((score_candidate(row, f), f) for f in folders), reverse=True)
        candidates = [(s, f) for s, f in scored if s >= 58 and f not in used_folders]
        for entry, candidate in zip(sorted(entries, key=lambda x: x["position"]), candidates):
            score, folder = candidate
            payload, mirror_filename = fetch_mirror_json(mirror, folder)
            if not payload:
                continue
            ok, obj = valid_json_bytes(payload)
            if not ok:
                continue
            base = safe_name(row.get("name") or row.get("title") or folder, 55)
            workflow_id_match = re.search(r"-(\d+)$", folder)
            workflow_id = workflow_id_match.group(1) if workflow_id_match else "semid"
            dest = JSON_DIR / f"{row_no:03d}_{entry['position']:02d}_{base}_mirror_{workflow_id}.json"
            dest.write_bytes(payload)
            entry["status"] = "recuperado_espelho_publico"
            entry["source"] = f"https://github.com/nusquama/n8nworkflows.xyz/tree/main/workflows/{folder}"
            entry["match_score"] = round(score, 2)
            entry["json_file"] = str(dest.relative_to(OUT))
            entry["validation"] = "workflow_n8n_validado" if is_n8n_workflow(obj) else "json_valido_estrutura_nao_confirmada"
            entry["error"] = entry["error"] + " | original inacessível; substituído por correspondência pública"
            used_folders.add(folder)

        for entry in entries:
            if entry["status"] == "pendente_fallback":
                top_preview = "; ".join(f"{s:.1f}:{f}" for s, f in scored[:3])
                entry["status"] = "indisponivel"
                entry["error"] += f" | nenhuma correspondência pública segura; melhores candidatos: {top_preview}"


def write_outputs(data: dict[str, Any], manifest: list[dict[str, Any]]) -> None:
    manifest.sort(key=lambda x: (int(x["excel_row"]), int(x["position"]), x.get("json_file") or ""))
    MANIFEST_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "excel_row","position","name","title","creator","youtube_id","original_drive_url",
        "drive_id","status","source","match_score","json_file","validation","error"
    ]
    with MANIFEST_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(manifest)

    counts: dict[str, int] = {}
    for m in manifest:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    json_files = sorted(JSON_DIR.glob("*.json"))
    text = f"""PACOTE DE JSONs n8n

Fonte da lista: n8n Templates.xlsx
Linhas da planilha: {data['row_count']}
Links únicos do Google Drive: {data['drive_url_count']}
Arquivos JSON válidos incluídos: {len(json_files)}

Status por link:
{json.dumps(counts, ensure_ascii=False, indent=2)}

Critério:
1. O script tentou baixar cada link original do Google Drive.
2. Cada conteúdo foi validado como JSON antes de entrar no pacote.
3. Quando o original estava removido, restrito ou não era JSON, foi procurada uma correspondência de alta confiança no arquivo público nusquama/n8nworkflows.xyz.
4. Nenhuma correspondência fraca foi incluída silenciosamente. Tudo consta em manifest.csv e manifest.json.

Pasta json/: arquivos importáveis no n8n.
manifest.csv: auditoria amigável.
manifest.json: auditoria estruturada.
"""
    README.write_text(text, encoding="utf-8")

    if FINAL_ZIP.exists():
        FINAL_ZIP.unlink()
    with zipfile.ZipFile(FINAL_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(OUT.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(OUT))
    log(f"ZIP criado: {FINAL_ZIP} ({FINAL_ZIP.stat().st_size} bytes)")
    log(text)


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(OUT, ignore_errors=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(INPUT.read_text(encoding="utf-8"))
    manifest, _ = direct_downloads(data)
    apply_fallback(data, manifest)
    write_outputs(data, manifest)
    if not FINAL_ZIP.exists() or FINAL_ZIP.stat().st_size == 0:
        raise RuntimeError("ZIP final não foi criado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
