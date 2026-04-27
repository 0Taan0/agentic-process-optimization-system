# control/gates.py
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import uuid
from typing import Dict, Tuple

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def write_gate_event(
    sdl,                      # SharedDataLayer-Instanz
    sid: str,                 # Session-ID
    gate: str,                # "perception", "interpretation", ...
    payload: Dict,            # {"dq_status": "GREEN"}
    artefacts: Dict,          # {"clean_xes": "...", "integrated_events": "...", ...}
    event_type=None  # Optionaler Event-Typ, sonst <gate>.done
) -> Tuple[Path, Dict]:
    """
    Erzeugt ein Gate-Event nach Contract und speichert es unter:
    data/sdl/<sid>/control/<gate>.done.json
    """
    base_dir: Path = getattr(sdl, "base_dir", Path("data") / "sdl")
    base_dir = getattr(sdl, "base_dir", Path("data")/"sdl")
    control_dir = (base_dir / sid / "control"); control_dir.mkdir(parents=True, exist_ok=True)

    evt_type = event_type or f"{gate}.done"
    event = {
        "type": evt_type,
        "session_id": sid,
        "event_id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "payload": payload or {},
        "artefacts": artefacts or {},
    }
    # Dateiname entsprechend Typ
    suffix = evt_type.split(".", 1)[1] if "." in evt_type else "done"
    out_path = control_dir / f"{gate}.{suffix}.json"
    out_path.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path, event