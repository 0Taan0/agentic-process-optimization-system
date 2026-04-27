from __future__ import annotations
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone


# ---------- Utilities ----------

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


def _find_first(root: Path, patterns: List[str]) -> Optional[Path]:
    for pat in patterns:
        for p in sorted(root.glob(pat)):
            if p.is_file():
                return p
    return None


# ---------- Data model for explanation bundle ----------

@dataclass
class EvidenceLink:
    label: str
    path: str
    sha256: Optional[str] = None

@dataclass
class Driver:
    label: str
    detail: str
    evidence_key: Optional[str] = None

@dataclass
class RiskEntry:
    risk: str
    mitigation: Optional[str] = None

@dataclass
class ExplanationBundle:
    version: str
    sid: str
    created_at: str
    decision_brief: str
    drivers: List[Driver]
    why_not: List[str]
    risks: List[RiskEntry]
    assumptions: List[str]
    evidence_links: Dict[str, EvidenceLink]
    kpis: Dict[str, Any]
    top_measures: List[Dict[str, Any]]
    sources_meta: Dict[str, Dict[str, Any]]

    def to_json(self) -> str:
        # dataclasses to serializable dict
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = []
        lines.append(f"# Decision Brief\n")
        lines.append(self.decision_brief.strip() + "\n")
        lines.append("## Key Drivers")
        for i, dr in enumerate(self.drivers, 1):
            if dr.evidence_key and dr.evidence_key in self.evidence_links:
                ev = self.evidence_links[dr.evidence_key]
                lines.append(f"{i}. **{dr.label}** — {dr.detail}  ")
                lines.append(f"   _evidence:_ `{ev.path}`")
            else:
                lines.append(f"{i}. **{dr.label}** — {dr.detail}")
        if self.why_not:
            lines.append("\n## Considered but not recommended")
            for w in self.why_not:
                lines.append(f"- {w}")
        if self.risks:
            lines.append("\n## Risks & Mitigations")
            for r in self.risks:
                m = f" (mitigation: {r.mitigation})" if r.mitigation else ""
                lines.append(f"- {r.risk}{m}")
        if self.assumptions:
            lines.append("\n## Assumptions / Limits")
            for a in self.assumptions:
                lines.append(f"- {a}")
        if self.kpis:
            lines.append("\n## KPIs (Summary)")
            lines.append("```json")
            lines.append(json.dumps(self.kpis, ensure_ascii=False, indent=2))
            lines.append("```")
        if self.top_measures:
            lines.append("\n## Top Measures")
            for i, m in enumerate(self.top_measures, 1):
                lbl = m.get("label") or m.get("action") or f"measure-{i}"
                sc = m.get("score")
                lines.append(f"- **{lbl}** — score: {sc}")
        if self.evidence_links:
            lines.append("\n## Evidence Links")
            for k, ev in self.evidence_links.items():
                sha = f" (sha256: {ev.sha256[:8]}…)" if ev.sha256 else ""
                lines.append(f"- **{k}**: `{ev.path}`{sha}")
        lines.append("\n---\n")
        lines.append(f"sid: {self.sid} • created_at: {self.created_at} • version: {self.version}")
        return "\n".join(lines)


# ---------- Explainability Agent (deterministic, LLM-optional) ----------

class ExplainabilityAgent:
    """
    Deterministic explainer: bundles existing Evaluation artifacts (simulation, comparison,
    prioritization, executive summary) into a single human-friendly dossier (JSON + Markdown).

    - No LLM by default. Optionally accepts an `llm` callable for polishing the decision_brief.
    - Searches standard SDL folders under the session dir.
    """

    def __init__(self, sdl, llm: Optional[Any] = None):
        self.sdl = sdl
        self.llm = llm  # optional: callable(prompt:str)->str or OpenAI client wrapper

    # ---- public API ----
    def run(self, sid: str) -> Dict[str, Any]:
        sdir = self.sdl.get_session_dir(sid)
        eval_dir = sdir / "evaluation"
        gen_dir = sdir / "generation"
        comp = self._load_first(eval_dir, ["comparison*.json", "*comparison*.json"]) or {}
        prio = self._load_first(eval_dir, ["prioritization*.json", "*priorit*.json"]) or {}
        base = self._load_first(eval_dir, ["baseline*.json", "*baseline*.json"]) or {}
        execsum = self._load_first(eval_dir, ["*executive*summary*.json", "*summary*.json"]) or {}
        to_be = _find_first(gen_dir, ["tobe_*.bpmn", "*.bpmn"])  # evidence only

        bundle = self._build_bundle(
            sid=sid,
            comparison=comp,
            prioritization=prio,
            baseline=base,
            executive=execsum,
            to_be_path=to_be,
        )

        out_dir = eval_dir / "explainability"
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"explanation_bundle_{sid}.json"
        md_path = out_dir / f"explanation_bundle_{sid}.md"

        json_path.write_text(bundle.to_json(), encoding="utf-8")
        md_path.write_text(bundle.to_markdown(), encoding="utf-8")

        return {
            "status": "success",
            "sid": sid,
            "json": str(json_path),
            "markdown": str(md_path),
        }

    # ---- internals ----
    def _load_first(self, root: Path, patterns: List[str]) -> Optional[dict]:
        p = _find_first(root, patterns)
        return _read_json(p) if p else None

    def _build_bundle(
        self,
        sid: str,
        comparison: Dict[str, Any],
        prioritization: Dict[str, Any],
        baseline: Dict[str, Any],
        executive: Dict[str, Any],
        to_be_path: Optional[Path],
    ) -> ExplanationBundle:
        # --- KPIs summary ---
        kpis: Dict[str, Any] = {}
        if comparison:
            kpis["time_improvements"] = comparison.get("time_improvements")
            kpis["global_before"] = comparison.get("global_before")
            kpis["global_after"] = comparison.get("global_after")
            if "score" in comparison:
                kpis["quality_score"] = comparison.get("score")
        if baseline and "global" in baseline:
            kpis.setdefault("baseline_global", baseline.get("global"))

        # --- Drivers ---
        drivers: List[Driver] = []
        # from comparison deltas
        ti = (comparison or {}).get("time_improvements") or {}
        if isinstance(ti, dict):
            for k, v in ti.items():
                if isinstance(v, (int, float)):
                    label = k.replace("_", " ")
                    detail = f"Δ {label}: {v:+.1f}%"
                    drivers.append(Driver(label=label, detail=detail, evidence_key="comparison"))
        # from prioritization top measures
        top_measures = []
        measures = (prioritization or {}).get("measures") or (prioritization or {}).get("ranked_measures") or []
        if isinstance(measures, list):
            for m in measures[:5]:
                lbl = m.get("label") or m.get("action") or m.get("id")
                sc = m.get("score") or m.get("wsjf") or m.get("rice")
                top_measures.append({"label": lbl, "score": sc})
                if lbl:
                    drivers.append(Driver(label=str(lbl), detail=f"recommended (score={sc})", evidence_key="prioritization"))

        # --- Why-not (optional heuristics) ---
        why_not: List[str] = []
        rejected = (prioritization or {}).get("rejected") or []
        if isinstance(rejected, list):
            for r in rejected[:5]:
                rn = r.get("label") or r.get("action") or r
                why_not.append(f"Not recommended: {rn}")

        # --- Risks ---
        risks_list: List[RiskEntry] = []
        for r in ((prioritization or {}).get("risks") or []):
            if isinstance(r, dict):
                risks_list.append(RiskEntry(risk=r.get("risk", ""), mitigation=r.get("mitigation")))
            elif isinstance(r, str):
                risks_list.append(RiskEntry(risk=r))

        # --- Assumptions ---
        assumptions: List[str] = []
        if executive and isinstance(executive.get("assumptions"), list):
            assumptions = list(executive.get("assumptions"))

        # --- Decision brief ---
        brief = self._compose_brief(comparison, prioritization, executive)

        # --- Evidence & sources ---
        evidence: Dict[str, EvidenceLink] = {}
        sources_meta: Dict[str, Dict[str, Any]] = {}

        # attempt to locate canonical files in evaluation dir
        sdir = self.sdl.get_session_dir(sid)
        eval_dir = sdir / "evaluation"
        comp_p = _find_first(eval_dir, ["comparison*.json", "*comparison*.json"]) or Path("<missing>")
        prio_p = _find_first(eval_dir, ["prioritization*.json", "*priorit*.json"]) or Path("<missing>")
        base_p = _find_first(eval_dir, ["baseline*.json", "*baseline*.json"]) or Path("<missing>")

        evidence["comparison"] = EvidenceLink("Comparison Report", str(comp_p), _sha256_file(comp_p) if comp_p.exists() else None)
        evidence["prioritization"] = EvidenceLink("Prioritization", str(prio_p), _sha256_file(prio_p) if prio_p.exists() else None)
        if to_be_path:
            evidence["tobe_bpmn"] = EvidenceLink("To-Be BPMN", str(to_be_path), _sha256_file(to_be_path))
        evidence["baseline"] = EvidenceLink("Baseline Metrics", str(base_p), _sha256_file(base_p) if base_p.exists() else None)

        sources_meta = {
            "comparison": {"path": str(comp_p)},
            "prioritization": {"path": str(prio_p)},
            "baseline": {"path": str(base_p)},
            "tobe_bpmn": {"path": str(to_be_path) if to_be_path else None},
        }

        return ExplanationBundle(
            version="1.0.0",
            sid=sid,
            created_at=_now_iso(),
            decision_brief=brief,
            drivers=drivers,
            why_not=why_not,
            risks=risks_list,
            assumptions=assumptions,
            evidence_links=evidence,
            kpis=kpis,
            top_measures=top_measures,
            sources_meta=sources_meta,
        )

    def _compose_brief(self, comparison: Dict[str, Any], prioritization: Dict[str, Any], executive: Dict[str, Any]) -> str:
        # Prefer existing executive summary if present
        if executive and isinstance(executive.get("summary"), str):
            return executive["summary"].strip()

        # Build a compact deterministic brief
        score = (comparison or {}).get("score")
        ti = (comparison or {}).get("time_improvements") or {}
        mean_pct = ti.get("cycle_mean_pct") if isinstance(ti, dict) else None
        p90_pct = ti.get("cycle_p90_pct") if isinstance(ti, dict) else None

        parts: List[str] = []
        parts.append("Bottom line: ")
        if isinstance(mean_pct, (int, float)):
            parts.append(f"mean cycle time Δ = {mean_pct:+.1f}%")
        if isinstance(p90_pct, (int, float)):
            parts.append(f", P90 Δ = {p90_pct:+.1f}%")
        if isinstance(score, (int, float)):
            parts.append(f"; quality score = {score:.0f}/100")
        if not parts or len(parts) == 1:
            parts.append("no significant change detected")
        brief = "".join(parts)

        if self.llm:
            try:
                prompt = (
                    "Rewrite the following KPI bullet into a crisp executive sentence (max 2 lines).\n" \
                    "Keep numbers exactly as given.\n" \
                    f"Bullet: {brief}\n"
                )
                polish = self.llm(prompt)
                if isinstance(polish, str) and polish.strip():
                    return polish.strip()
            except Exception:
                pass
        return brief