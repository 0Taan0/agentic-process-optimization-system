# control/orchestrator.py
from __future__ import annotations
import json
import uuid
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import sys

# Projekt-Root in sys.path (falls als Skript gestartet)
sys.path.append(str(Path(__file__).resolve().parents[1]))

# old
from core.shared_data_layer import SharedDataLayer
# new
from core.mongo_sdl import MongoSDL
sdl = MongoSDL()

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

class Orchestrator:
    """
    MVP Control-Plane:
    - Rule-based routing Event -> Command
    - Idempotenz via event_id
    - Audit-Log (data/audit.jsonl)
    - Persistenter Session-State: data/sdl/<sid>/control/state.json
    """
    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = project_root or Path(__file__).resolve().parents[1]
        #self.sdl = SharedDataLayer(base_dir=self.project_root / "data" / "sdl")
        self.sdl = MongoSDL(base_dir=self.project_root / "data" / "sdl")

        self.audit_path = ensure_dir(self.project_root / "data").joinpath("audit.jsonl")
        self.sessions: Dict[str, Dict[str, Any]] = {}

    # --- Public API ---
    def handle_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self._validate_event(event)
        sid = event["session_id"]
        ev_id = event["event_id"]

        # load state
        state = self._load_state(sid)

        # 3) Idempotenz: Event nur einmal verarbeiten
        seen = state.setdefault("seen_events", [])
        if ev_id in seen:
            self._write_audit("duplicate_event_ignored", sid, {"event_id": ev_id, "type": event["type"]})
            return None

        # 4) Letztes Event merken (für Phasen-Guards / Debug)
        state["last_event"] = {
            "type": event["type"],
            "event_id": ev_id,
            "ts": now_iso(),
            "payload": event.get("payload", {}),
            "artefacts": event.get("artefacts", {}),
        }
        seen.append(ev_id)
        self._save_state(sid, state)

        command = self._route(event)

        # NEU: State neu laden, weil _route() ihn auf Platte geändert haben kann
        state = self._load_state(sid)

        if command:
            state["current_phase"] = command["target"]
            state["last_cmd"] = {
                "type": command["type"],
                "target": command["target"],
                "action": command["action"],
                "correlation_id": command["correlation_id"],
                "ts": now_iso(),
            }
            self._write_command(sid, command)
            self._write_audit("transition", sid, {"from_event": event["type"], "to_cmd": f"{command['target']}.{command['action']}"})
        else:
            self._write_audit("session_end", sid, {"because": f"{event['type']} / decision: {event.get('payload', {}).get('decision_type')}"})

        self._save_state(sid, state)

        self.sessions[sid] = state
        return command

    def handle_event_file(self, event_path: Path) -> Optional[Dict[str, Any]]:
        with open(event_path, "r", encoding="utf-8") as f:
            event = json.load(f)
        return self.handle_event(event)

    def _route(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        etype = event["type"]
        sid = event["session_id"]
        payload = event.get("payload", {}) or {}
        
        # Load state for tracking iterations
        state = self._load_state(sid)
        
        # Track generation iterations (for versioning)
        gen_iteration = int(state.get("generation_iteration", 0))
        
        if etype == "perception.done":
            return self._cmd(sid, target="interpretation")
        
        if etype == "interpretation.done":
            # Reset iteration counter for new process
            state["generation_iteration"] = 0
            self._save_state(sid, state)
            return self._cmd(sid, target="generation")
        
        if etype == "generation.done":
            return self._cmd(sid, target="evaluation")
        
        '''if etype == "evaluation.done":
            # Check if this is the first evaluation
            eval_count = int(state.get("evaluation_count", 0))
            state["evaluation_count"] = eval_count + 1
            self._save_state(sid, state)
            
            if eval_count == 0:  # First evaluation
                # Automatically go back to generation for v2
                state["generation_iteration"] = 1
                self._save_state(sid, state)
                return self._cmd(sid, target="generation")
            else:
                # After second evaluation, go to exploration
                return self._cmd(sid, target="exploration")'''
        if etype == "evaluation.done":
            # State laden
            state_path = self.sdl.get_session_dir(sid) / "control" / "state.json"
            state = {}
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    state = {}

            # Zähle Evaluationen im State (nicht via Payload)
            eval_count = int(state.get("evaluation_count", 0))
            state["evaluation_count"] = eval_count + 1
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

            if eval_count == 0:
                # 1. Evaluation -> Generation (v2)
                state["generation_iteration"] = int(state.get("generation_iteration", 0)) + 1
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                return self._cmd(sid, target="generation")
            else:
                # 2. Evaluation -> Exploration
                return self._cmd(sid, target="exploration")


        if etype == "insights.done":
            return self._cmd(sid, target="feedback")
        
        if etype == "decision.done":
            d = (payload.get("decision_type") or "").lower()
            
            if d == "approve":
                self._write_audit("workflow_completed", sid, {"decision": "approved"})
                return None  # End workflow
                
            if d == "request_changes":
                # Increment generation iteration for v3, v4, etc.
                gen_iteration = int(state.get("generation_iteration", 0))
                state["generation_iteration"] = gen_iteration + 1
                self._save_state(sid, state)
                return self._cmd(sid, target="generation")
                
            if d == "re_simulate":
                return self._cmd(sid, target="evaluation")
                
            if d == "rescope":
                # Full reset for new scope
                state["generation_iteration"] = 0
                state["evaluation_count"] = 0
                self._save_state(sid, state)
                return self._cmd(sid, target="perception")
                
            # Unknown decision type
            self._write_audit("unknown_decision", sid, {"decision": d})
            return None
        '''if etype == "decision.done":
            d = (payload.get("decision_type") or "").lower()

            if d == "approve":
                return None  # Ende

            if d == "request_changes":
                # Max. 1 Iteration zurück zur Generation
                if gen_loops >= 1:
                    return None  # Ende statt Loop
                state["gen_loops"] = gen_loops + 1
                self._save_state(sid, state)
                return self._cmd(sid, target="generation")

            if d == "re_simulate":
                return self._cmd(sid, target="evaluation")

            if d == "rescope":
                return self._cmd(sid, target="perception")

            # Default (unbekannte/fehlende Entscheidung): stoppen
            return None'''

        self._write_audit("unknown_event_type", sid, {"type": etype})
        return None



    def _cmd(self, sid: str, target: str) -> Dict[str, Any]:
        return {
            "type": "cmd",
            "target": target,
            "action": "start",
            "session_id": sid,
            "correlation_id": str(uuid.uuid4()),
            "ts": now_iso(),
        }

    def _validate_event(self, event: Dict[str, Any]) -> None:
        req = ["type", "session_id", "event_id"]
        missing = [k for k in req if k not in event]
        if missing:
            raise ValueError(f"Invalid event, missing: {missing}. Got: {event}")

    def _session_control_dir(self, sid: str) -> Path:
        base = self.sdl.base_dir if hasattr(self.sdl, "base_dir") else (self.project_root / "data" / "sdl")
        return ensure_dir(base / sid / "control")

    def _state_path(self, sid: str) -> Path:
        return self._session_control_dir(sid) / "state.json"

    def _cmd_out_path(self, sid: str, target: str) -> Path:
        return self._session_control_dir(sid) / f"cmd_{target}_start.json"

    def _load_state(self, sid: str) -> Dict[str, Any]:
        p = self._state_path(sid)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"session_id": sid, "current_phase": None, "last_event": None, "last_cmd": None, "seen_events": []}

    def _save_state(self, sid: str, state: Dict[str, Any]) -> None:
        p = self._state_path(sid)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_command(self, sid: str, command: Dict[str, Any]) -> Path:
        p = self._cmd_out_path(sid, command["target"])
        p.write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def _write_audit(self, kind: str, session_id: str, details: Dict[str, Any]) -> None:
        rec = {"ts": now_iso(), "kind": kind, "session_id": session_id, "details": details}
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

# --- CLI / Quick test ---
def main():
    parser = argparse.ArgumentParser(description="MVP Orchestrator")
    parser.add_argument("--event", type=str, help="Pfad zu einem Event-JSON (optional).")
    parser.add_argument("--sid", type=str, help="Session-ID (für Demo-Event).")
    parser.add_argument("--etype", type=str, help="Event-Typ (z.B. perception.done)")
    args = parser.parse_args()

    orch = Orchestrator()

    if args.event:
        cmd = orch.handle_event_file(Path(args.event))
        print("Command:", cmd)
