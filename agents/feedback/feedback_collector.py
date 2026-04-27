from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

# This collector is UI-agnostic. It:
# 1) Loads the Exploration insights manifest to prepare a payload for display
# 2) Validates a user's decision + optional change_request
# 3) Persists the decision under evaluation/feedback and returns a decision.done event


# Utilities

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(p: Path, obj: dict) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p



# Collector


class FeedbackCollector:
    """
    Minimal feedback collector for the Exploration phase.

    Works with any SDL that either exposes `get_session_dir(sid)` or has `sdl_root`.
    """

    def __init__(self, sdl):
        self.sdl = sdl

    # --------------- Public API ---------------
    def prepare(self, sid: str) -> Dict[str, Any]:
        """Return a compact payload for the UI: paths to explanation and visuals.
        Always best-effort; never raises on missing files.
        """
        sdir = self._resolve_session_dir(sid)
        eval_dir = sdir / "evaluation"
        insights_dir = eval_dir / "insights"

        manifest = self._find_first(insights_dir, [f"insights_manifest_{sid}.json", "insights_manifest*.json"])
        man = _read_json(manifest) if manifest else None
        if not man:
            # Best-effort fallback (no manifest)
            man = {
                "sid": sid,
                "explanation": {
                    "json": str(self._find_first(eval_dir / "explainability", ["*.json"]) or "<missing>"),
                    "md": str(self._find_first(eval_dir / "explainability", ["*.md"]) or "<missing>")
                },
                "visuals": [str(p) for p in (eval_dir / "visualization").glob("*.png")],
                "issues": ["insights manifest missing"],
            }
        return {
            "sid": sid,
            "manifest": man,
            "ready": True,
        }

    def submit(self,
               sid: str,
               decision_type: str,
               change_request: Optional[Dict[str, Any]] = None,
               rationale: str = "") -> Dict[str, Any]:
        """Validate, persist and return a decision.done event payload.
        `decision_type`: approve | request_changes | re_simulate | rescope
        """
        decision_type = (decision_type or "").lower().strip()
        if decision_type not in {"approve", "request_changes", "re_simulate", "rescope"}:
            return self._error("invalid_decision_type", f"Unsupported decision_type: {decision_type}")

        # Validate change request only when relevant
        cr = change_request or {}
        issues = []
        if decision_type in {"request_changes", "re_simulate"} and cr:
            ok, issues = self._validate_change_request(sid, cr)
            if not ok:
                return self._error("invalid_change_request", issues)

        # Persist
        sdir = self._resolve_session_dir(sid)
        fb_dir = sdir / "evaluation" / "feedback"
        obj = {
            "sid": sid,
            "decision_type": decision_type,
            "change_request": cr,
            "rationale": rationale or "",
            "created_at": _now_iso(),
        }
        out = fb_dir / f"decision_{sid}.json"
        _write_json(out, obj)
        import uuid
        # Event for orchestrator
        event = {
            "type": "decision.done",
            "session_id": sid,
            "event_id": str(uuid.uuid4()),  # <-- hinzufügen
            "payload": obj,
        }
        return {
            "status": "success",
            "sid": sid,
            "path": str(out),
            "event": event,
        }

    # --------------- Validation ---------------
    def _validate_change_request(self, sid: str, cr: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Lightweight schema + ID existence checks against To-Be BPMN and suggestions.* files."""
        errors: List[str] = []

        # shape
        if not isinstance(cr, dict):
            return False, ["change_request must be an object"]

        apply = cr.get("apply")
        if apply is None:
            apply = cr.get("actions")  # allow synonym
        if not isinstance(apply, list):
            return False, ["change_request.apply must be an array"]

        # Load sources for ID checks
        sdir = self._resolve_session_dir(sid)
        tobe = self._find_first(sdir / "generation", ["tobe_*.bpmn", "*.bpmn"])  # latest to-be
        sugg = self._find_first(sdir / "generation", ["improvement_suggestions*.json", "*suggestions*.json"])
        bpmn_ids = self._collect_bpmn_ids(tobe) if tobe else set()
        suggestion_ids = self._collect_suggestion_ids(sugg) if sugg else set()

        for i, action in enumerate(apply, 1):
            if not isinstance(action, dict):
                errors.append(f"apply[{i}] must be an object")
                continue
            atype = action.get("action")
            if not isinstance(atype, str):
                errors.append(f"apply[{i}].action missing or not a string")
                continue

            # Normalize keys
            atype_l = atype.lower().strip()

            if atype_l == "update_timer":
                target = action.get("target")
                dur = action.get("duration")
                if not target or not isinstance(target, str):
                    errors.append(f"apply[{i}].target (timer id) required")
                if not dur or not isinstance(dur, str) or not dur.startswith("PT"):
                    errors.append(f"apply[{i}].duration must be ISO-8601 like PT48H")
                if target and bpmn_ids and target not in bpmn_ids:
                    errors.append(f"apply[{i}].target '{target}' not found in BPMN ids")

            elif atype_l == "rename_task":
                target = action.get("target")
                new_label = action.get("new_label")
                if not target or not isinstance(target, str):
                    errors.append(f"apply[{i}].target (task id) required")
                if not new_label or not isinstance(new_label, str):
                    errors.append(f"apply[{i}].new_label required")
                if target and bpmn_ids and target not in bpmn_ids:
                    errors.append(f"apply[{i}].target '{target}' not found in BPMN ids")

            elif atype_l == "disable_suggestion":
                sug_id = action.get("id")
                if not sug_id or not isinstance(sug_id, str):
                    errors.append(f"apply[{i}].id (suggestion id) required")
                if sug_id and suggestion_ids and sug_id not in suggestion_ids:
                    errors.append(f"apply[{i}].id '{sug_id}' not found in suggestions")

            elif atype_l in {"merge_tasks", "split_task", "enable_parallel", "disable_parallel", "set_gateway_rule", "attach_annotation", "adjust_resource"}:
                # Skeleton checks only (optional, extend later)
                # We accept them but don't deep-validate in MVP
                pass

            else:
                errors.append(f"apply[{i}].action '{atype}' not supported")

        return (len(errors) == 0), errors

    # Internals 
    def _resolve_session_dir(self, sid: str) -> Path:
        if hasattr(self.sdl, "get_session_dir"):
            p = self.sdl.get_session_dir(sid)
            return Path(p) if not isinstance(p, Path) else p
        if hasattr(self.sdl, "sdl_root"):
            return Path(self.sdl.sdl_root) / sid
        return Path("data") / "sdl" / sid

    def _find_first(self, root: Path, patterns: List[str]) -> Optional[Path]:
        for pat in patterns:
            for p in sorted(root.glob(pat)):
                if p.is_file():
                    return p
        return None

    def _collect_bpmn_ids(self, bpmn_path: Path) -> set:
        try:
            root = ET.parse(str(bpmn_path)).getroot()
            ids = set()
            for el in root.iter():
                _id = el.attrib.get("id")
                if _id:
                    ids.add(_id)
            return ids
        except Exception:
            return set()

    def _collect_suggestion_ids(self, sugg_path: Path) -> set:
        try:
            data = _read_json(sugg_path) or {}
            ids = set()
            items = data if isinstance(data, list) else data.get("suggestions") or data.get("time") or []
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        _id = it.get("id") or it.get("suggestion_id")
                        if isinstance(_id, str):
                            ids.add(_id)
            return ids
        except Exception:
            return set()

    def _error(self, code: str, details: Any) -> Dict[str, Any]:
        return {"status": "error", "error": {"code": code, "details": details}}
    
    def interactive_cli(self, sid: str, orchestrator=None) -> Dict[str, Any]:
        """Zeigt kompakt die Exploration-Ergebnisse und fragt im Terminal die Entscheidung ab.
        Gibt das decision.done-Event zurück (wird NICHT automatisch an den Orchestrator gesendet).
        """
        payload = self.prepare(sid)
        man = payload.get("manifest") or {}
        print("\n=== FEEDBACK: EXPLORATION INSIGHTS ===")
        print("SID:", sid)

        # Kurzer Überblick
        expl = man.get("explanation", {})
        print("\nExplanation:")
        print("  JSON:", expl.get("json"))
        print("  MD:  ", expl.get("md"))

        visuals = man.get("visuals") or []
        print("\nVisuals:")
        for v in visuals:
            print("  -", v)

        issues = man.get("issues") or []
        if issues:
            print("\nIssues:")
            for i in issues:
                print("  -", i)
                
        '''approve: Orchestrator beendet den Lauf (kein neues cmd_* File). 
        request_changes: Orchestrator schreibt control/cmd_generation_start.json → Generation läuft erneut (mit deinem change_request, siehe unten). 
        re_simulate: Orchestrator schreibt control/cmd_evaluation_start.json → nur Evaluation erneut. 
        rescope: Orchestrator schreibt control/cmd_perception_start.json → zurück zum Start (neue Daten/Scope). '''

        # Entscheidung abfragen
        print("\nDecision? [approve | request_changes | re_simulate | rescope]")
        while True:
            d = input("> ").strip().lower()
            if d in {"approve", "request_changes", "re_simulate", "rescope"}:
                break
            print("Bitte eine der Optionen eingeben: approve | request_changes | re_simulate | rescope")

        cr = {}
        if d in {"request_changes", "re_simulate"}:
            print("\n(optional) Change Request als JSON eingeben (leer lassen für keinen):")
            txt = input("> ").strip()
            if txt:
                try:
                    cr = json.loads(txt)
                except Exception as e:
                    print("⚠️  Ungültiges JSON, CR wird ignoriert:", e)
                    cr = {}

        print("\n(optional) Rationale/Begründung (leer möglich):")
        rationale = input("> ").strip()

        # At the end, after creating the result:
        res = self.submit(sid, decision_type=d, change_request=cr, rationale=rationale)
        
        if res.get("status") != "success":
            print("Fehler:", res.get("error"))
            return res

        print("\n Entscheidung gespeichert:", res.get("path"))
        
        # Automatically send to orchestrator if provided
        if orchestrator and "event" in res:
            print(" Sende Event an Orchestrator...")
            cmd = orchestrator.handle_event(res["event"])
            if cmd:
                print(f" Nächster Schritt: {cmd['target']}.{cmd['action']}")
            else:
                print(" Workflow beendet.")
        else:
            print("Entscheidung gespeichert. Nächster Schritt wird automatisch enqueued.")
        
        return res




'''
class EnhancedFeedbackCollector(FeedbackCollector):
    def __init__(self, sdl, impact_analyzer=None, rule_engine=None):
        super().__init__(sdl)
        self.impact_analyzer = impact_analyzer
        self.rule_engine = rule_engine
        
    def submit_with_analysis(self, sid: str, decision: Dict) -> Dict:
        """Submit with impact analysis and rule checking"""
        # First analyze
        if decision.get("decision_type") == "request_changes":
            impact = self.analyze_impact(sid, decision.get("change_request", {}))
            if impact.get("high_risk_changes"):
                return self._require_additional_approval(sid, decision, impact)
        
        # Check business rules
        if self.rule_engine:
            violations = self.rule_engine.check(sid, decision)
            if violations:
                return self._error("rule_violations", violations)
                
        # Proceed with normal submit
        return super().submit(sid, **decision)
'''