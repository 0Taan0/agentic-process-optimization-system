from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union
from datetime import datetime
import uuid
import json
import os
import shutil
import re

# --- Basispfade (MVP: lokale Ordnerstruktur) ---
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT    = PROJECT_ROOT / "data"
SDL_ROOT     = DATA_ROOT / "sdl"

# Dateinamen-Konventionen innerhalb einer Session
F_CLEAN_XES    = "clean_xes.xes"
F_DQ_REPORT    = "dq_report.json"
F_SESSION_META = "session_meta.json"

# Session-ID-Konvention: sid-YYYYmmdd-HHMMSS-<8hex>
SID_REGEX = re.compile(r"^sid-\d{8}-\d{6}-[0-9a-f]{8}$")

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _atomic_write_bytes(target: Path, data: Union[bytes, bytearray, memoryview]) -> None:
    _ensure_dir(target.parent)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)

def _atomic_write_text(target: Path, text: str) -> None:
    _ensure_dir(target.parent)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)

def _atomic_write_json(target: Path, obj: Dict[str, Any]) -> None:
    _atomic_write_text(target, json.dumps(obj, ensure_ascii=False, indent=2))

def _read_json(p: Path) -> Dict[str, Any]:
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def _read_bytes(p: Path) -> bytes:
    with open(p, "rb") as f:
        return f.read()

def _new_sid() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"sid-{ts}-{short}"

def _assert_sid(sid: str) -> None:
    if not SID_REGEX.match(sid):
        raise ValueError(f"Ungültige Session-ID: {sid}")

class SharedDataLayer:
    """
    Dünne, dateibasierte SDL-API (MVP).
    Später leicht gegen DB/Blob-Storage austauschbar.
    Ordnerstruktur:
      data/sdl/<sid>/
        uploads/               # Rohuploads
        clean_xes.xes          # validiertes Eventlog
        dq_report.json         # Data-Quality-Report
        session_meta.json      # Metadaten zur Session
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir is None:
            self.project_root = Path(__file__).resolve().parents[1]
            self.data_root = self.project_root / "data"
            self.sdl_root = self.data_root / "sdl"
        else:
            self.sdl_root = Path(base_dir)
            self.data_root = self.sdl_root.parent
            self.project_root = self.data_root.parent
        _ensure_dir(self.data_root)
        _ensure_dir(self.sdl_root)

    def _base(self, sid: str) -> Path:
        _assert_sid(sid)
        return self.sdl_root / sid


    # ---------- Session ----------
    def create_session(self, meta: Optional[Dict[str, Any]] = None) -> str:
        sid = _new_sid()
        base = self._base(sid)
        _ensure_dir(base)
        _ensure_dir(base / "uploads")
        self.save_session_meta(sid, meta or {})
        return sid

    def list_sessions(self) -> Iterable[str]:
        if not self.sdl_root.exists():
            return []
        return sorted(
            p.name for p in self.sdl_root.iterdir()
            if p.is_dir() and p.name.startswith("sid-")
        )

    def _base(self, sid: str) -> Path:
        _assert_sid(sid)
        return self.sdl_root / sid

    # ---------- Session Meta ----------
    def save_session_meta(self, sid: str, meta: Dict[str, Any]) -> Path:
        p = self._base(sid) / F_SESSION_META
        _atomic_write_json(p, meta)
        return p

    def read_session_meta(self, sid: str) -> Dict[str, Any]:
        p = self._base(sid) / F_SESSION_META
        return _read_json(p)

    # ---------- Uploads (Rohdaten) ----------
    def save_upload_file(self, sid: str, file_path: Union[str, Path]) -> Path:
        base = self._base(sid)
        uploads = base / "uploads"
        _ensure_dir(uploads)
        src = Path(file_path)
        dst = uploads / src.name
        shutil.copy2(src, dst)
        return dst

    def save_upload_bytes(self, sid: str, filename: str, data: Union[bytes, bytearray, memoryview]) -> Path:
        base = self._base(sid)
        uploads = base / "uploads"
        dst = uploads / filename
        _atomic_write_bytes(dst, data)
        return dst

    def list_uploads(self, sid: str) -> Iterable[Path]:
        uploads = self._base(sid) / "uploads"
        if not uploads.exists():
            return []
        return sorted(uploads.iterdir())

    # ---------- Cleaned XES ----------
    def save_clean_xes(self, sid: str, source: Union[bytes, bytearray, memoryview, str, Path]) -> Path:
        target = self._base(sid) / F_CLEAN_XES
        if isinstance(source, (bytes, bytearray, memoryview)):
            _atomic_write_bytes(target, source)
        else:
            src = Path(source)
            _ensure_dir(target.parent)
            shutil.copy2(src, target)
        return target

    def read_clean_xes(self, sid: str) -> bytes:
        return _read_bytes(self._base(sid) / F_CLEAN_XES)

    # ---------- DQ Report ----------
    def save_dq_report(self, sid: str, report: Dict[str, Any]) -> Path:
        p = self._base(sid) / F_DQ_REPORT
        _atomic_write_json(p, report)
        return p

    def read_dq_report(self, sid: str) -> Dict[str, Any]:
        return _read_json(self._base(sid) / F_DQ_REPORT)
