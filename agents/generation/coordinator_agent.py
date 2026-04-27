from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Union, Optional

from core.mongo_sdl import MongoSDL
from agents.generation.io import (
    path_requirements, path_constraints,
    save_json_and_record, T_CONSTRAINTS
)

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

class ProcessGuidelineCoordinatorAgent:
    """
    Vereinheitlicht Requirements (goals, soft_constraints) mit (vorläufigen kleinen) Constraints.
    - Liest:  generation/requirements_<sid>.json
             generation/constraints_<sid>.json
    - Schreibt: generation/constraints_<sid>.json
    - Spiegelt nach Mongo: phase="generation", type="constraints"
    """

    def __init__(self, sdl: MongoSDL):
        self.sdl = sdl

    #  Soft-Constraints normalisieren (strings oder dicts zu dicts) 
    def _normalize_soft(self, sc: Optional[List[Union[str, Dict[str, Any]]]]) -> List[Dict[str, Any]]:
        if not sc:
            return []
        out: List[Dict[str, Any]] = []
        for item in sc:
            if isinstance(item, dict):
                obj: Dict[str, Any] = {}
                if "metric" in item:
                    obj["metric"] = str(item["metric"])
                if "target_pct" in item:
                    try:
                        obj["target_pct"] = float(item["target_pct"])
                    except Exception:
                        # still skip bad numeric values without inventing data
                        pass
                if "rule" in item:
                    obj["rule"] = str(item["rule"])
                if obj:
                    out.append(obj)
            else:
                out.append({"rule": str(item)})
        return out

    # harte Defaults sicherstellen (ohne Fantasiewerte)
    def _ensure_hard_defaults(self, hard: Optional[Dict[str, Any]]) -> Dict[str, bool]:
        base = {
            "must_have_start_event": True,
            "must_have_end_event": True,
            "must_have_task": True
        }
        if isinstance(hard, dict):
            # vorhandene Flags respektieren 
            base.update({k: bool(v) for k, v in hard.items()})
        return base

    def run(self, sid: str) -> Dict[str, Any]:
        # Inputs laden
        req_path = path_requirements(sid, self.sdl)
        if not req_path.exists():
            raise FileNotFoundError(f"requirements not found: {req_path}")
        requirements = json.loads(req_path.read_text(encoding="utf-8"))

        constr_path = path_constraints(sid, self.sdl)
        if constr_path.exists():
            existing = json.loads(constr_path.read_text(encoding="utf-8"))
        else:
            existing = {"sid": sid, "created_at": _now_iso()}


        goals = list(requirements.get("goals") or [])
        soft_rules = self._normalize_soft(requirements.get("soft_constraints"))
        hard_constraints = self._ensure_hard_defaults(existing.get("hard_constraints"))

        merged = dict(existing)  # bestehende Felder behalten
        merged["sid"] = sid
        merged["created_at"] = _now_iso()
        merged["based_on"] = {
            "requirements_path": str(req_path),
            "previous_constraints_path": str(constr_path) if constr_path.exists() else None
        }
        merged["goals"] = goals
        merged["soft_rules"] = soft_rules
        merged["hard_constraints"] = hard_constraints
        # Reihenfolge der Goals = Priorisierung (keine erfundenen Gewichte)
        merged["priority"] = {"goals_order": goals}

        #speichern + Mongo  (overwrite=True: konsolidierte Version ist Quelle) 
        save_json_and_record(
            self.sdl,
            sid,
            T_CONSTRAINTS,
            constr_path,
            merged,
            summary={
                "hard_constraints_count": len([k for k, v in hard_constraints.items() if v]),
                "soft_rules_count": len(soft_rules),
                "goals_count": len(goals),
            },
            overwrite=True
        )

        return {"constraints_path": str(constr_path), "constraints": merged}
