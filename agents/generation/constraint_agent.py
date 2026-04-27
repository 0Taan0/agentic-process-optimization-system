from datetime import datetime, timezone
import json
from core.mongo_sdl import MongoSDL
from agents.generation.io import (
    path_requirements,
    path_constraints,
    save_json_and_record,
    T_CONSTRAINTS,
)

def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

class ConstraintAgent:
    """
    Liest requirements_<sid>.json und ergänzt harte Regeln.
    Beispiel: mindestens ein StartEvent, ein EndEvent, ein Task.
    """

    def __init__(self, sdl: MongoSDL):
        self.sdl = sdl

    def run(self, sid: str):
        # Pfad zum Requirements-File (über IO-Helper)
        req_path = path_requirements(sid, self.sdl)
        if not req_path.exists():
            raise FileNotFoundError(f"Requirements file not found: {req_path}")

        # Requirements laden
        requirements = json.loads(req_path.read_text(encoding="utf-8"))

        # Harte Constraints definieren
        constraints = {
            "sid": sid,
            "created_at": _now_iso(),
            "from_requirements": requirements.get("goals", []),
            "hard_constraints": {
                "must_have_start_event": True,
                "must_have_end_event": True,
                "must_have_task": True,
            },
        }

        # Datei schreiben + Mongo spiegeln
        constr_path = path_constraints(sid, self.sdl)
        save_json_and_record(
            self.sdl,
            sid,
            T_CONSTRAINTS,
            constr_path,
            constraints,
            summary={
                "hard_constraints_count": len(constraints["hard_constraints"])
            },
        )

        return {"constraints_path": str(constr_path), "constraints": constraints}