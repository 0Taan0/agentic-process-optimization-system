# core/mongo_sdl.py
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union, List
from datetime import datetime, timezone
import shutil
import uuid
import json
import os
import re

from pymongo.collection import Collection
from .mongo_client import get_mongo

# -------------------------------
# Compatible path/name constants (same as file-based SDL)
# -------------------------------
F_CLEAN_XES    = "clean_xes.xes"       # we also keep a <sid>-suffixed variant for compatibility
F_DQ_REPORT    = "dq_report.json"
F_SESSION_META = "session_meta.json"

# Session-ID pattern: sid-YYYYmmdd-HHMMSS-<8hex>
SID_REGEX = re.compile(r"^sid-\d{8}-\d{6}-[0-9a-f]{8}$")


# -------------------------------
# Small FS utilities (same logic as file SDL)
# -------------------------------
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

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def _new_sid() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"sid-{ts}-{short}"

def _assert_sid(sid: str) -> None:
    if not SID_REGEX.match(sid):
        raise ValueError(f"Ungültige Session-ID: {sid}")


# =======================================
# Mongo-backed Shared Data Layer (Hybrid)
# =======================================
class MongoSDL:
    """
    Hybrid SDL:
      - Keeps the same FS layout (data/sdl/<sid>/...) for raw uploads & some artefacts
      - Mirrors session/upload/artefact metadata to MongoDB

    Compatibility fields for existing code:
      - self.sdl_root  -> base folder for sessions
      - self.base_dir  -> alias (some code expects base_dir)
      - get_session_dir(sid) -> helper used by other components
    """

    # -------- constructor --------
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        # Prepare filesystem base (compatible with old SDL)
        if base_dir is None:
            self.project_root = Path(__file__).resolve().parents[1]
            self.data_root = self.project_root / "data"
            self.sdl_root = self.data_root / "sdl"
        else:
            self.sdl_root = Path(base_dir)
            self.data_root = self.sdl_root.parent
            self.project_root = self.data_root.parent

        self.base_dir = self.sdl_root  # alias for compatibility
        _ensure_dir(self.data_root)
        _ensure_dir(self.sdl_root)

        # Init Mongo
        client, db_name = get_mongo()
        db = client[db_name]
        self.col_sessions: Collection = db["sessions"]
        self.col_uploads:  Collection = db["uploads"]
        self.col_artefacts: Collection = db["artefacts"]
        # NOTE: If/when you store models in Mongo, add e.g.:
        # self.col_models: Collection = db["models"]  # see TODO at bottom

        # Minimal helpful indexes
        self.col_sessions.create_index("created_at")
        self.col_uploads.create_index([("sid", 1), ("stored_at", 1)])
        self.col_artefacts.create_index([("sid", 1), ("phase", 1), ("type", 1), ("ts", 1)])

    # -------- path helpers --------
    def _base(self, sid: str) -> Path:
        _assert_sid(sid)
        return self.sdl_root / sid

    def get_session_dir(self, sid: str) -> Path:
        """Helper: base folder for a given session id."""
        return self._base(sid)

    # ========== Session ==========
    def create_session(self, meta: Optional[Dict[str, Any]] = None) -> str:
        sid = _new_sid()
        base = self._base(sid)
        _ensure_dir(base)
        _ensure_dir(base / "uploads")

        # FS: session_meta.json
        fs_meta = (meta or {}).copy()
        fs_meta.setdefault("created_at", _now_iso())
        _atomic_write_json(base / F_SESSION_META, fs_meta)

        # Mongo: sessions (idempotent via _id)
        doc = {
            "_id": sid,
            "created_at": fs_meta.get("created_at"),
            "updated_at": fs_meta.get("created_at"),
            "meta": fs_meta,
        }
        self.col_sessions.insert_one(doc)
        return sid

    def list_sessions(self) -> Iterable[str]:
        """List session folders from FS (keeps compatibility)."""
        if not self.sdl_root.exists():
            return []
        return sorted(
            p.name for p in self.sdl_root.iterdir()
            if p.is_dir() and p.name.startswith("sid-")
        )

    def save_session_meta(self, sid: str, meta: Dict[str, Any]) -> Path:
        # FS
        p = self._base(sid) / F_SESSION_META
        _atomic_write_json(p, meta)

        # Mongo mirror
        self.col_sessions.update_one(
            {"_id": sid},
            {"$set": {"meta": meta, "updated_at": _now_iso()}},
            upsert=True,
        )
        return p

    def read_session_meta(self, sid: str) -> Dict[str, Any]:
        # Source remains FS to keep other agents unchanged
        p = self._base(sid) / F_SESSION_META
        return _read_json(p)

    # ========== Uploads ==========
    def save_upload_file(self, sid: str, file_path: Union[str, Path]) -> Path:
        base = self._base(sid)
        uploads = base / "uploads"
        _ensure_dir(uploads)
        src = Path(file_path)
        dst = uploads / src.name
        shutil.copy2(src, dst)

        # Mongo: upload metadata
        self.col_uploads.insert_one({
            "sid": sid,
            "kind": "file",
            "filename": dst.name,
            "path": str(dst),
            "size_bytes": dst.stat().st_size if dst.exists() else None,
            "stored_at": _now_iso(),
        })
        return dst

    def save_upload_bytes(self, sid: str, filename: str, data: Union[bytes, bytearray, memoryview]) -> Path:
        base = self._base(sid)
        uploads = base / "uploads"
        dst = uploads / filename
        _atomic_write_bytes(dst, data)

        # Mongo: upload metadata
        self.col_uploads.insert_one({
            "sid": sid,
            "kind": "bytes",
            "filename": dst.name,
            "path": str(dst),
            "size_bytes": dst.stat().st_size if dst.exists() else None,
            "stored_at": _now_iso(),
        })
        return dst

    def list_uploads(self, sid: str) -> Iterable[Path]:
        uploads = self._base(sid) / "uploads"
        if not uploads.exists():
            return []
        return sorted(uploads.iterdir())

    # ======== Cleaned XES ========
    def save_clean_xes(self, sid: str, source: Union[bytes, bytearray, memoryview, str, Path]) -> Path:
        """
        Store clean XES to two FS locations for compatibility:
        - data/sdl/<sid>/perception/clean_xes_<sid>.xes   (concrete)
        - data/sdl/<sid>/perception/clean_xes.xes         (generic)
        and mirror an artefact record in Mongo.
        Uses atomic writes and avoids shutil.copy2 to prevent Windows file locks.
        """
        base = self._base(sid) / "perception"
        _ensure_dir(base)
        p1 = base / f"clean_xes_{sid}.xes"
        p2 = base / F_CLEAN_XES

        # read bytes once (handles Path or bytes input)
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
        else:
            src = Path(source)
            # try a tiny retry loop in case the writer hasn't flushed/closed yet
            last_err = None
            for _ in range(3):
                try:
                    with open(src, "rb") as f:
                        data = f.read()
                    last_err = None
                    break
                except PermissionError as e:
                    last_err = e
                    import time as _t; _t.sleep(0.05)
            if last_err:
                raise last_err

        # atomic write to both targets
        _atomic_write_bytes(p1, data)
        _atomic_write_bytes(p2, data)

        # Mongo artefact entry
        self.col_artefacts.insert_one({
            "sid": sid,
            "phase": "perception",
            "type": "clean_xes",
            "path": str(p1),
            "ts": _now_iso(),
            "alt_paths": [str(p2)]
        })
        return p1

    def read_clean_xes(self, sid: str) -> bytes:
        # Prefer the generic filename
        p = self._base(sid) / "perception" / F_CLEAN_XES
        if not p.exists():
            p = self._base(sid) / "perception" / f"clean_xes_{sid}.xes"
        return _read_bytes(p)

    # ========= DQ Report =========
    def save_dq_report(self, sid: str, report: Dict[str, Any]) -> Path:
        base = self._base(sid) / "perception"
        _ensure_dir(base)
        p = base / F_DQ_REPORT
        _atomic_write_json(p, report)

        # Mongo (store a compact summary for quick querying)
        summary = {
            "qc_pass": report.get("qc_pass"),
            "stats": report.get("stats"),
            "issues": report.get("issues"),
            "counters": report.get("counters")
        }
        self.col_artefacts.insert_one({
            "sid": sid,
            "phase": "perception",
            "type": "dq_report",
            "path": str(p),
            "ts": _now_iso(),
            "summary": summary
        })
        return p
    
        # ========== Generic artefact recorder ==========
    def record_artefact(
        self,
        sid: str,
        phase: str,
        artefact_type: str,
        path: Union[str, Path],
        *,
        summary: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Spiegel einen Artefakt-Eintrag in Mongo (Collection: artefacts).
        - sid: Session-ID
        - phase: z.B. "perception", "interpretation", "generation"
        - artefact_type: z.B. "integrated_eventlog", "clean_xes", "dq_report", "ist_bpmn"
        - path: FS-Pfad zum Artefakt
        - summary/extra: optionale kleine JSON-Snippets für schnelle Queries
        """
        doc = {
            "sid": sid,
            "phase": phase,
            "type": artefact_type,
            "path": str(path),
            "ts": _now_iso(),
        }
        if summary:
            doc["summary"] = summary
        if extra:
            doc["extra"] = extra
        self.col_artefacts.insert_one(doc)




    def read_dq_report(self, sid: str) -> Dict[str, Any]:
        p = self._base(sid) / "perception" / F_DQ_REPORT
        return _read_json(p)
    
        # ========= Interpretation Quality =========
    def save_interpretation_quality(self, sid: str, quality: Dict[str, Any]) -> Path:
        """
        Speichert die Quality-Analyse des As-Is-Modells:
        - is_bpmn_valid (bool)
        - kpi_baseline (dict mit z. B. #Tasks, #Events, etc.)
        Legt JSON-Datei im FS ab und spiegelt eine kompakte Version in Mongo.
        """
        base = self._base(sid) / "interpretation"
        _ensure_dir(base)
        p = base / "as_is_quality.json"
        _atomic_write_json(p, quality)

        summary = {
            "is_bpmn_valid": quality.get("is_bpmn_valid"),
            "kpi_baseline": quality.get("kpi_baseline")
        }

        self.col_artefacts.insert_one({
            "sid": sid,
            "phase": "interpretation",
            "type": "as_is_quality",
            "path": str(p),
            "ts": _now_iso(),
            "summary": summary
        })
        return p

    def read_interpretation_quality(self, sid: str) -> Dict[str, Any]:
        """
        Lädt die zuletzt gespeicherte Quality-Analyse aus dem FS.
        """
        p = self._base(sid) / "interpretation" / "as_is_quality.json"
        return _read_json(p)
    
        # --------- kleine Log-Helfer (für Agents) ---------
    def record_error(self, sid: str, phase: str, message: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Schreibt einen Fehler-Eintrag als Artefakt (einfach & querybar)."""
        doc = {
            "sid": sid,
            "phase": phase,
            "type": "log_error",
            "ts": _now_iso(),
            "summary": {"message": message},
        }
        if extra:
            doc["extra"] = extra
        self.col_artefacts.insert_one(doc)

    def record_warning(self, sid: str, phase: str, message: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        """Schreibt einen Warn-Eintrag als Artefakt."""
        doc = {
            "sid": sid,
            "phase": phase,
            "type": "log_warning",
            "ts": _now_iso(),
            "summary": {"message": message},
        }
        if extra:
            doc["extra"] = extra
        self.col_artefacts.insert_one(doc)



    #   (when you migrate models to Mongo) 
    # def save_model_version_doc(self, sid: str, model_id: str, version: str,
    #                            bpmn_xml: str, hash_bpmn: str, extra_meta: Optional[Dict[str, Any]] = None) -> str:
    #     """
    #     Store a model version as a single Mongo document (text fields, <=16MB).
    #     Returns inserted_id as string.
    #     """
    #     doc = {
    #         "_type": "model_version",
    #         "sid": sid,
    #         "model_id": model_id,
    #         "version": version,
    #         "bpmn_xml": bpmn_xml,       # full XML as text
    #         "hash_bpmn": hash_bpmn,
    #         "created_at": _now_iso(),
    #         **(extra_meta or {})
    #     }
    #     res = self.col_models.insert_one(doc)
    #     return str(res.inserted_id)
