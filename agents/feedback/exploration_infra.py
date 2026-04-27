from __future__ import annotations
import json
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pathlib import Path
import json
import logging


from agents.feedback.exploration.visualization_agent import VisualizationAgent
from agents.feedback.exploration.explainability_agent import ExplainabilityAgent


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(p: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(p: Path, obj: dict) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


#  Manifest Model 

@dataclass
class InsightsManifest:
    version: str
    sid: str
    created_at: str
    explanation: Dict[str, str]  # {json, md}
    visuals: List[str]
    sources: Dict[str, str]
    issues: List[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


#  Exploration Infra 

class ExplorationInfra:
    """
    Koordiniert Explainability + Visualization als eigenständige Phase "Exploration".
    - Läuft nach Evaluation.
    - Schreibt alles in SDL unter: evaluation/explainability, evaluation/visualization, evaluation/insights.
    - Liefert ein zentrales Manifest für den Feedback Collector.
    - Nimmt Feedback entgegen und persistiert es (Bidirektionalität).
    """

    def __init__(self, sdl, explainer: Optional[ExplainabilityAgent] = None, viz: Optional[VisualizationAgent] = None):
        self.sdl = sdl
        self.explainer = explainer or ExplainabilityAgent(sdl)
        from pathlib import Path

        sid = getattr(sdl, "sid", getattr(sdl, "session_id", "unknown"))
        # versuche einen Session-Ordner aus dem SDL zu bekommen
        session_dir = (
            Path(getattr(sdl, "session_dir", "")) 
            if hasattr(sdl, "session_dir") else
            Path(getattr(getattr(sdl, "paths", None), "session_dir", "")) 
            if hasattr(sdl, "paths") else
            Path(".")  # Fallback
        )
        out_dir = session_dir / "presentation" / f"viz_{sid}"
        out_dir.mkdir(parents=True, exist_ok=True)

        self.viz = viz or VisualizationAgent(out_dir)


    # -------- public API --------
    def run(self, sid: str) -> Dict[str, Any]:
        """Führt Explainability + Visualization aus und schreibt ein Insights-Manifest."""
        sdir = self._resolve_session_dir(sid)
        eval_dir = sdir / "evaluation"
        (eval_dir / "insights").mkdir(parents=True, exist_ok=True)

        issues: List[str] = []

        # 1) Explainability
        exp_out = self.explainer.run(sid)
        exp_json = Path(exp_out.get("json", "")) if exp_out else None
        exp_md = Path(exp_out.get("markdown", "")) if exp_out else None
        if not exp_out or not (exp_json and exp_json.exists()):
            issues.append("explainability: missing bundle")

        # 2) Visualization (mit echten Vergleichsdaten)
        comp_path = self._find_first(eval_dir, [f"comparison_report_{sid}.json",
                                                "comparison_report*.json", "*comparison*.json"])
        base_path = self._find_first(eval_dir, [f"baseline_metrics_{sid}.json",
                                                "baseline_metrics*.json", "*baseline*.json"])
        prio_path = self._find_first(eval_dir, [f"prioritization_{sid}.json",
                                                "prioritization*.json", "*priorit*.json"])

        comparison     = _read_json(Path(comp_path)) if comp_path else None
        baseline       = _read_json(Path(base_path)) if base_path else None
        prioritization = _read_json(Path(prio_path)) if prio_path else None

        if not comparison:
            # Ohne comparison_report keine Visualisierung – sauber loggen & skippen
            issues.append("visualization: missing comparison_report → skipped")
            viz_out = {"artifacts": [], "issues": ["missing comparison_report"]}
        else:
            viz_out = self.viz.run(
                sid,
                comparison=comparison,
                baseline=baseline,
                prioritization=prioritization,
            )

        visual_paths = [str(Path(p)) for p in viz_out.get("artifacts", [])] if viz_out else []
        if viz_out and viz_out.get("issues"):
            issues.extend(viz_out.get("issues"))



        # 3) Quellen (für UI/Audit)
        sources = {
            "comparison": self._find_first(eval_dir, ["comparison*.json", "*comparison*.json"]),
            "prioritization": self._find_first(eval_dir, ["prioritization*.json", "*priorit*.json"]),
            "baseline": self._find_first(eval_dir, ["baseline*.json", "*baseline*.json"]),
            "tobe_bpmn": self._find_first(sdir / "generation", ["tobe_*.bpmn", "*.bpmn"]),
        }
        sources = {k: str(v) if isinstance(v, Path) else "<missing>" for k, v in sources.items()}

        # 4) Manifest schreiben
        explanation_dict = {
            "json": str(exp_json) if exp_json else "<missing>",
            "md": str(exp_md) if exp_md else "<missing>",
        }
        man = InsightsManifest(
            version="1.0.0",
            sid=sid,
            created_at=_now_iso(),
            explanation=explanation_dict,
            visuals=visual_paths,
            sources=sources,
            issues=issues,
        )
        man_path = eval_dir / "insights" / f"insights_manifest_{sid}.json"
        _write_json(man_path, json.loads(man.to_json()))
        import uuid
        # Optional: Event-ähnliche Rückgabe für Orchestrator
        return {
            "status": "success",
            "sid": sid,
            "manifest": str(man_path),
            "explanation": explanation_dict,
            "visuals": visual_paths,
            "issues": issues,
            "event": {
                "type": "insights.done",
                "session_id": sid,
                "event_id": str(uuid.uuid4()),
                "payload": {"manifest_path": str(man_path)}
            }
        }

    def prepare_feedback_payload(self, sid: str) -> Dict[str, Any]:
        """Stellt dem Feedback Collector ein kompaktes Paket bereit (Pfade + kurze Meta)."""
        sdir = self._resolve_session_dir(sid)
        eval_dir = sdir / "evaluation"
        man = self._find_first(eval_dir / "insights", [f"insights_manifest_{sid}.json", "insights_manifest*.json"]) or None
        man_obj = _read_json(man) if man else None

        if not man_obj:
            # Falls der Aufrufer etwas haben will, trotzdem best-effort liefern
            man_obj = {
                "sid": sid,
                "explanation": {
                    "json": str(self._find_first(eval_dir / "explainability", ["*.json"]) or "<missing>"),
                    "md": str(self._find_first(eval_dir / "explainability", ["*.md"]) or "<missing>"),
                },
                "visuals": [str(p) for p in (eval_dir / "visualization").glob("*.png")],
                "issues": ["insights manifest missing"],
            }
        return {
            "sid": sid,
            "manifest": man_obj,
            "ready": True,
        }

    def receive_feedback(self, sid: str, decision_type: str, change_request: Optional[Dict[str, Any]] = None,
                         rationale: str = "") -> Dict[str, Any]:
        """
        Nimmt Feedback aus dem Interaction Layer entgegen und speichert es auditierbar.
        Gibt ein Orchestrator-kompatibles Event-Objekt zurück (decision.done).
        """
        sdir = self._resolve_session_dir(sid)
        fb_dir = sdir / "evaluation" / "feedback"
        fb_dir.mkdir(parents=True, exist_ok=True)

        decision_obj = {
            "sid": sid,
            "decision_type": (decision_type or "").lower(),
            "change_request": change_request or {},
            "rationale": rationale or "",
            "created_at": _now_iso(),
        }
        outp = fb_dir / f"decision_{sid}.json"
        _write_json(outp, decision_obj)
        import uuid
        return {
            "status": "success",
            "sid": sid,
            "path": str(outp),
            "event": {
                "type": "decision.done",
                "session_id": sid,
                "event_id": str(uuid.uuid4()),
                "payload": decision_obj,
            }
        }

    # -------- internals --------
    def _resolve_session_dir(self, sid: str) -> Path:
        # 1) SDL mit get_session_dir
        if hasattr(self.sdl, "get_session_dir"):
            p = self.sdl.get_session_dir(sid)
            return Path(p) if not isinstance(p, Path) else p
        # 2) SharedDataLayer: sdl_root
        if hasattr(self.sdl, "sdl_root"):
            return Path(self.sdl.sdl_root) / sid
        # 3) Fallback Standardpfad
        return Path("data") / "sdl" / sid

    def _find_first(self, root: Path, patterns: List[str]) -> Optional[Path]:
        for pat in patterns:
            for p in sorted(root.glob(pat)):
                if p.is_file():
                    return p
        return None
