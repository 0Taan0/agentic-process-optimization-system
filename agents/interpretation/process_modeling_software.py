import os
import json
import hashlib
import secrets
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, List
import logging

# Import the improved DI handler
from interpretation.improved_bpmn_di import ensure_valid_bpmn_di

logger = logging.getLogger(__name__)


# Index für Modelle
class ModelIndex:
    """
    Diese Klasse kümmert sich um den "Index" – eine JSONL-Datei (eine Zeile = ein Eintrag).
    Warum? → Damit wir alle Versionen von Modellen zentral verwalten und durchsuchen können.
    """

    def __init__(self, repo_dir: Path):
        """
        Initialisierung: legt das Basisverzeichnis und die Index-Datei an.
        """
        self.repo_dir = Path(repo_dir)
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.repo_dir / "index.jsonl"

        # Falls Index noch nicht existiert → leere Datei anlegen
        if not self.index_path.exists():
            self.index_path.write_text("", encoding="utf-8")

    def _now_iso(self) -> str:
        """Aktueller Zeitstempel im ISO-Format (UTC)."""
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _hash_file(self, path: Path) -> str:
        """SHA256-Hash einer Datei berechnen (für BPMN-Dateien)."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _load_index(self) -> List[Dict]:
        """Lädt alle Zeilen (Einträge) aus der Indexdatei."""
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines if l.strip()]

    def _save_index(self, entries: List[Dict]) -> None:
        """Überschreibt die Indexdatei mit allen Einträgen."""
        with open(self.index_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def get_version(self, model_id: str, version: str) -> Optional[Dict]:
        """
        Liefert genau EINEN Index-Eintrag für (model_id, version)
        oder None, falls nicht vorhanden.
        """
        for e in self._load_index():
            if e["model_id"] == model_id and e["version"] == version:
                return e
        return None

    def list_variants(self, parent_model_id: str) -> List[Dict]:
        """
        Liefert alle Modelle/Versionen, die als Variante von parent_model_id markiert sind.
        (Nützlich für: "alle Varianten zu parent")
        """
        return [
            e for e in self._load_index()
            if e.get("parent_model_id") == parent_model_id
        ]
    
    # Public API
    def add_entry(self, entry: Dict) -> None:
        """Hängt einen neuen Eintrag an den Index an (JSONL-Format)."""
        with open(self.index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_latest(self, model_id: str) -> Optional[Dict]:
        """Holt die neueste Version eines bestimmten Modells."""
        entries = [e for e in self._load_index() if e["model_id"] == model_id]
        if not entries:
            return None
        # Sortiert nach Version (z. B. v0003 ist neuer als v0002)
        return max(entries, key=lambda e: e["version"])

    def get_versions(self, model_id: str) -> List[Dict]:
        """Holt alle Versionen eines Modells, sortiert nach Version."""
        entries = [e for e in self._load_index() if e["model_id"] == model_id]
        return sorted(entries, key=lambda e: e["version"])

    def get_by_session(self, session_id: str) -> List[Dict]:
        """Holt alle Modelle, die zu einer bestimmten Session gehören."""
        return [e for e in self._load_index() if e["session_id"] == session_id]


# Hauptklasse: BPMN Repository
class BPMNRepository:
    """
    Diese Klasse kümmert sich um:
    - BPMN-Dateien speichern (als Versionen)
    - Hashing und Idempotenz prüfen
    - Versionierung verwalten
    - Index-Einträge schreiben
    """
    
    def __init__(self, base_dir: str = "data/models"):
        # Basisordner (z. B. data/models)
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # Index-Klasse instanziieren
        self.index = ModelIndex(self.base_dir)

    # Hilfsmethoden
    def _now(self) -> str:
        """Aktueller UTC-Zeitstempel im ISO-Format."""
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _new_model_id(self) -> str:
        """Generiert eine neue zufällige Modell-ID, z. B. mod_ab12cd34."""
        return f"mod_{secrets.token_hex(4)}"

    def _hash_bpmn(self, bpmn_xml: str) -> str:
        """Berechnet den SHA-256-Hash einer BPMN-XML."""
        normalized = bpmn_xml.strip().encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()

    def _validate_bpmn(self, bpmn_xml: str) -> bool:
        """
        Prüft, ob die BPMN-XML wohlgeformt ist.
        → Wichtig, damit keine kaputten XML-Dateien ins Repo gelangen.
        """
        try:
            ET.fromstring(bpmn_xml)
            return True
        except ET.ParseError as e:
            logger.error(f"BPMN validation failed: {e}")
            return False

    def _next_version(self, model_dir: Path) -> str:
        """
        Ermittelt die nächste Versionsnummer (z. B. v0002).
        """
        existing = sorted([p.stem for p in model_dir.glob("v*.bpmn")])
        if not existing:
            return "v0001"
        last = existing[-1]
        num = int(last[1:])  # "v0003" → 3
        return f"v{num+1:04d}"
    
    def get_latest(self, model_id: str) -> Optional[Dict]:
        """
        Wrapper um den Index: neueste Version eines Modells.
        Enthält Pfad + Meta (aus dem Index).
        """
        return self.index.get_latest(model_id)

    def get_version(self, model_id: str, version: str) -> Optional[Dict]:
        """
        Wrapper um den Index: eine spezifische Version.
        """
        return self.index.get_version(model_id, version)

    def list_versions(self, model_id: str) -> List[Dict]:
        """
        Wrapper um den Index: alle Versionen eines Modells (aufsteigend).
        """
        return self.index.get_versions(model_id)

    def list_variants(self, parent_model_id: str) -> List[Dict]:
        """
        Wrapper um den Index: alle Versionen, die Varianten eines Parent-Modells sind.
        """
        return self.index.list_variants(parent_model_id)
    
    # Public API
    def create_model(self, session_id: str, bpmn_xml: str,
                     source: str = "as_is",
                     origin_agent: str = "interpreter",
                     label: Optional[str] = None,
                     parent_model_id: Optional[str] = None,
                     tags: Optional[list] = None) -> dict:
        """
        Legt ein neues Modell mit Version v0001 an.
        """
        # First ensure the BPMN has valid DI
        logger.info("Ensuring BPMN has valid DI...")
        bpmn_xml = ensure_valid_bpmn_di(bpmn_xml)
        
        if not self._validate_bpmn(bpmn_xml):
            raise ValueError("Ungültiges BPMN XML – nicht parsebar.")

        # Neue Model-ID + Ordner
        model_id = self._new_model_id()
        model_dir = self.base_dir / model_id
        model_dir.mkdir(parents=True, exist_ok=True)

        version = "v0001"
        file_path = model_dir / f"{version}.bpmn"

        # Sicheres Schreiben (tmp → rename)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(bpmn_xml)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, file_path)

        # Hash über die finale (DI-angereicherte) XML berechnen
        hash_bpmn = self._hash_bpmn(bpmn_xml)

        # Index-Eintrag erzeugen
        entry = {
            "model_id": model_id,
            "version": version,
            "session_id": session_id,
            "source": source,
            "label": label or f"{model_id}_{version}",
            "origin_agent": origin_agent,
            "parent_model_id": parent_model_id,
            "tags": tags or [],
            "created_at": self._now(),
            "hash_bpmn": hash_bpmn,
            "bpmn_xml_path": str(file_path),
        }
        self.index.add_entry(entry)

        logger.info(f"Created model {model_id} version {version}")
        return {"status": "created", **entry}

    def upsert_model(self, model_id: str, session_id: str, bpmn_xml: str,
                     source: str = "as_is",
                     origin_agent: str = "interpreter",
                     label: str = "",
                     parent_model_id: Optional[str] = None,
                     tags: Optional[list] = None,
                     force_new_version: bool = False) -> dict:
        """
        Aktualisiert ein Modell oder legt eine neue Version an.
        Regeln:
        - Wenn XML identisch (Hash gleich) → keine neue Version.
        - Wenn geändert oder force_new_version=True → neue Version v000X.
        """
        # First ensure the BPMN has valid DI
        logger.info("Ensuring BPMN has valid DI...")
        bpmn_xml = ensure_valid_bpmn_di(bpmn_xml)
        
        if not self._validate_bpmn(bpmn_xml):
            raise ValueError("Ungültiges BPMN XML – nicht parsebar.")

        model_dir = self.base_dir / model_id
        model_dir.mkdir(parents=True, exist_ok=True)

        # Hash über die finale (DI-angereicherte) XML
        new_hash = self._hash_bpmn(bpmn_xml)

        # Letzte Version prüfen
        latest = self.index.get_latest(model_id)
        if latest and latest["hash_bpmn"] == new_hash and not force_new_version:
            # Keine Änderung → gleiche Version zurückgeben
            return {"status": "unchanged", **latest}

        # Neue Version bestimmen
        new_version = self._next_version(model_dir)
        file_path = model_dir / f"{new_version}.bpmn"

        # Sicheres Schreiben (tmp → rename)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            tmp.write(bpmn_xml)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, file_path)

        # Index-Eintrag erzeugen
        entry = {
            "model_id": model_id,
            "version": new_version,
            "session_id": session_id,
            "source": source,
            "label": label or f"{model_id}_{new_version}",
            "origin_agent": origin_agent,
            "parent_model_id": parent_model_id,
            "tags": tags or [],
            "created_at": self._now(),
            "hash_bpmn": new_hash,
            "bpmn_xml_path": str(file_path),
        }
        self.index.add_entry(entry)

        logger.info(f"Updated model {model_id} to version {new_version}")
        return {"status": "new_version", **entry}