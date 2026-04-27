from __future__ import annotations
import json
import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from core.mongo_sdl import MongoSDL
from agents.evaluation.simulation.simulation import compute_baseline_metrics, simulate_tobe_metrics
from agents.evaluation.comparison.comparison import ProcessComparator
from agents.evaluation.agents.improved_evaluation_prioritization_agent import PrioritizationAgent

logger = logging.getLogger(__name__)


class EvaluationEngine:
    """
    Orchestrates the complete evaluation phase:
    1. Compute baseline metrics from XES
    2. Simulate TO-BE process
    3. Compare AS-IS vs TO-BE
    4. Optionally run prioritization for implementation planning
    5. Generate final recommendation
    """
    
    def __init__(self, sdl: MongoSDL, orchestrator=None):
        self.sdl = sdl
        self.orchestrator = orchestrator
        self.comparator = ProcessComparator(sdl)
        self.prioritization_agent = PrioritizationAgent(sdl, llm_model="gpt-5-mini")

    def _find_as_is_bpmn(self, sid: str) -> Optional[Path]:
        """Find AS-IS BPMN from interpretation phase"""
        interp = self.sdl.get_session_dir(sid) / "interpretation"
        pref = interp / f"ist_bpmn_{sid}.bpmn"
        if pref.exists():
            return pref
        cands = sorted(interp.glob("*.bpmn"), key=lambda p: p.stat().st_mtime, reverse=True)
        return cands[0] if cands else None

    def _find_to_be_bpmn(self, sid: str) -> Optional[Path]:
        """Find latest TO-BE BPMN from generation phase"""
        gen = self.sdl.get_session_dir(sid) / "generation"

        # Fallback 1: generation_summary
        summ = gen / f"generation_summary_{sid}.json"
        if summ.exists():
            try:
                j = json.loads(summ.read_text(encoding="utf-8"))
                p = j.get("tobe_bpmn")
                if p and Path(p).exists():
                    return Path(p)
            except Exception:
                pass

        # Fallback 2: all tobe_bpmn_* files, sorted by version + timestamp
        def extract_version(p: Path) -> tuple[int, float]:
            name = p.stem
            import re
            match = re.search(r'_v(\d+)_', name)
            version = int(match.group(1)) if match else 0
            return (version, p.stat().st_mtime)

        tobe_files = list(gen.glob("tobe_bpmn_*.bpmn"))
        if not tobe_files:
            return None

        tobe_files.sort(key=extract_version, reverse=True)
        return tobe_files[0]


    def _find_xes(self, sid: str) -> Optional[Path]:
        """Find XES event log"""
        # 1) From perception.done artefacts
        ctrl = self.sdl.get_session_dir(sid) / "control"
        for ev in sorted(ctrl.glob("perception.done*.json")):
            try:
                j = json.loads(ev.read_text(encoding="utf-8"))
                artefacts = j.get("artefacts", {})
                p = artefacts.get("clean_xes")
                if p and Path(p).exists():
                    return Path(p)
            except Exception:
                pass
        
        # 2) Direct in perception folder
        perception_dir = self.sdl.get_session_dir(sid) / "perception"
        if perception_dir.exists():
            # Search for any XES files
            for pattern in ["*.xes", "*.xes.gz", "*.XES", "*.XES.gz"]:
                cands = list(perception_dir.glob(pattern))
                if cands:
                    return cands[0]
        
        # 3) In session-level uploads folder (correct location)
        session_uploads_dir = self.sdl.get_session_dir(sid) / "uploads"
        if session_uploads_dir.exists():
            for pattern in ["*.xes", "*.xes.gz", "*.XES", "*.XES.gz"]:
                cands = list(session_uploads_dir.glob(pattern))
                if cands:
                    return cands[0]

        # 4) (Legacy) perception/uploads for backward compatibility
        legacy_uploads_dir = (self.sdl.get_session_dir(sid) / "perception" / "uploads")
        if legacy_uploads_dir.exists():
            for pattern in ["*.xes", "*.xes.gz", "*.XES", "*.XES.gz"]:
                cands = list(legacy_uploads_dir.glob(pattern))
                if cands:
                    return cands[0]

        return None

    def _find_objectives(self, sid: str) -> Optional[Path]:
        """Find objectives from generation phase"""
        gen = self.sdl.get_session_dir(sid) / "generation"
        for name in (f"objectives_{sid}.json", "objectives.json"):
            p = gen / name
            if p.exists():
                return p
        return None

    def _find_constraints(self, sid: str) -> Optional[Path]:
        """Find consolidated constraints from generation phase"""
        gen = self.sdl.get_session_dir(sid) / "generation"
        # Try consolidated first, then regular constraints
        for name in (f"consolidated_constraints_{sid}.json", f"constraints_{sid}.json"):
            p = gen / name
            if p.exists():
                return p
        return None

    def _find_resource_config(self, sid: str) -> Optional[Path]:
        """Find resource configuration if available"""
        for base in [self.sdl.get_session_dir(sid), 
                     self.sdl.get_session_dir(sid) / "config",
                     self.sdl.get_session_dir(sid) / "generation"]:
            if base.exists():
                for name in ["resource_config.json", f"resource_config_{sid}.json"]:
                    p = base / name
                    if p.exists():
                        return p
        return None
    def _calculate_time_improvements(
        self,
        baseline: Dict[str, Any],
        sim_metrics: Dict[str, Any]
    ) -> Dict[str, float]:
        """Calculate time improvements WITH validation"""
        base_global = baseline.get("global", {})
        
        improvements = {}
        
        # Get values
        base_mean = base_global.get("cycle_mean_s", 0)
        sim_mean = sim_metrics.get("cycle_mean_s", 0)
        base_p90 = base_global.get("cycle_p90_s", 0)
        sim_p90 = sim_metrics.get("cycle_p90_s", 0)
        
        if base_mean > 0:
            improvements["cycle_mean_pct"] = ((base_mean - sim_mean) / base_mean) * 100
        else:
            improvements["cycle_mean_pct"] = None  # ← Explizit None
            logger.warning("No baseline cycle_mean_s - cannot calculate improvement percentage")
        
        if base_p90 > 0:
            improvements["cycle_p90_pct"] = ((base_p90 - sim_p90) / base_p90) * 100
        else:
            improvements["cycle_p90_pct"] = None
            logger.warning("No baseline cycle_p90_s - cannot calculate improvement percentage")
        
        # Absolute values (always show)
        improvements["cycle_mean_delta_s"] = sim_mean - base_mean
        improvements["cycle_p90_delta_s"] = sim_p90 - base_p90
        
        # Context values for transparency
        improvements["baseline_mean_s"] = base_mean
        improvements["baseline_p90_s"] = base_p90
        improvements["simulated_mean_s"] = sim_mean
        improvements["simulated_p90_s"] = sim_p90
        
        return improvements
    def _find_improvement_plan(self, sid: str) -> Optional[Path]:
        """Find improvement plan from generation phase"""
        gen = self.sdl.get_session_dir(sid) / "generation"
        plan_path = gen / f"improvement_plan_{sid}.json"
        if plan_path.exists():
            return plan_path
        # Fallback to any improvement suggestions
        suggestions = list(gen.glob("improvement_suggestions_*.json"))
        return suggestions[0] if suggestions else None

    def _is_first_evaluation(self, sid: str) -> bool:
        """Check if this is the first evaluation cycle"""
        ctrl_dir = self.sdl.get_session_dir(sid) / "control"
        # Count how many evaluation.done events exist
        eval_done_events = list(ctrl_dir.glob("evaluation.done*.json"))
        return len(eval_done_events) == 0

    def run(self, sid: str, run_prioritization: bool = True) -> Dict[str, Any]:
        """
        Run complete evaluation: simulation + comparison + optional prioritization
        
        Args:
            sid: Session ID
            run_prioritization: Whether to run prioritization agent (default: True)
            
        Returns:
            Dictionary with all evaluation results and paths
        """
        logger.info(f"Starting evaluation for session {sid}")

        # Check evaluation count from state
        state_path = self.sdl.get_session_dir(sid) / "control" / "state.json"
        eval_count = 0
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                eval_count = int(state.get("evaluation_count", 0))
            except Exception as e:
                logger.warning(f"Could not read evaluation_count: {e}")
                eval_count = 0

        # 1. Find all required files
        as_is = self._find_as_is_bpmn(sid)
        to_be = self._find_to_be_bpmn(sid)
        xes = self._find_xes(sid)
        
        if not (as_is and to_be and xes):
            raise FileNotFoundError(
                f"Missing required inputs: as_is={as_is}, to_be={to_be}, xes={xes}"
            )
        
        objectives_path = self._find_objectives(sid)
        constraints_path = self._find_constraints(sid)
        resource_config_path = self._find_resource_config(sid)
        
        # 2. Compute baseline metrics
        logger.info("Computing baseline metrics from XES")
        baseline = compute_baseline_metrics(xes, as_is)
        
        # 3. Simulate TO-BE process
        logger.info("Simulating TO-BE process")
        tobe_sim = simulate_tobe_metrics(
            to_be, 
            baseline, 
            objectives_path=objectives_path,
            resource_config=self._load_resource_config(resource_config_path)
        )
        
        # 4. Save simulation results
        eval_dir = self.sdl.get_session_dir(sid) / "evaluation"
        eval_dir.mkdir(parents=True, exist_ok=True)
        
        base_path = eval_dir / f"baseline_metrics_{sid}.json"
        sim_path = eval_dir / f"sim_tobe_metrics_{sid}.json"
        
        base_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        sim_path.write_text(json.dumps(tobe_sim, ensure_ascii=False, indent=2), encoding="utf-8")
        
        # Record simulation artifacts
        self.sdl.record_artefact(
            sid=sid,
            phase="evaluation",
            artefact_type="baseline_metrics",
            path=base_path,
            summary={
                "cycle_mean_s": baseline.get("global", {}).get("cycle_mean_s"),
                "activities": len(baseline.get("per_activity", {}))
            }
        )
        
        self.sdl.record_artefact(
            sid=sid,
            phase="evaluation",
            artefact_type="sim_tobe_metrics",
            path=sim_path,
            summary={
                "cycle_mean_s": tobe_sim.get("cycle_mean_s"),
                "on_time_p90": tobe_sim.get("on_time_p90")
            }
        )
        
        # 5. Run comparison
        logger.info("Running process comparison")
        comparison_result = self.comparator.compare(
            sid=sid,
            as_is_bpmn_path=as_is,
            to_be_bpmn_path=to_be,
            baseline_metrics_path=base_path,
            sim_metrics_path=sim_path,
            objectives_path=objectives_path,
            constraints_path=constraints_path,
            resource_config_path=resource_config_path
        )
        
        # Extract comparison report path
        comparison_report_path = None
        if isinstance(comparison_result, dict) and "comparison_report_path" in comparison_result:
            comparison_report_path = Path(comparison_result["comparison_report_path"])
        elif isinstance(comparison_result, dict):
            # Fallback: look for the report in evaluation folder
            report_candidates = list(eval_dir.glob(f"comparison_report_{sid}.json"))
            if report_candidates:
                comparison_report_path = report_candidates[0]
        
        # 6. Optionally run prioritization agent
        prioritization_result = None
        if run_prioritization:
            recommendation_type = comparison_result.get("recommendation", {}).get("type", "")
            
            # Only run prioritization if not recommending to reiterate
            if recommendation_type != "re_iterate_generation":
                logger.info("Running prioritization agent for implementation planning")
                try:
                    improvement_plan_path = self._find_improvement_plan(sid)
                    
                    prioritization_result = self.prioritization_agent.evaluate_and_prioritize(
                        sid=sid,
                        as_is_bpmn_path=as_is,
                        to_be_bpmn_path=to_be,
                        baseline_metrics_path=base_path,
                        sim_metrics_path=sim_path,
                        comparison_report_path=comparison_report_path,
                        objectives_path=objectives_path,
                        constraints_path=constraints_path,
                        improvement_plan_path=improvement_plan_path,
                        resource_config_path=resource_config_path
                    )
                    logger.info("Prioritization completed successfully")
                except Exception as e:
                    logger.error(f"Prioritization failed: {str(e)}", exc_info=True)
                    prioritization_result = {
                        "status": "error",
                        "error": str(e)
                    }
            else:
                logger.info("Skipping prioritization due to reiteration recommendation")
                prioritization_result = {
                    "status": "skipped",
                    "reason": "Comparison recommends re-iteration"
                }
        
        # 7. Create overall evaluation summary
        evaluation_summary = self._create_evaluation_summary(
            sid=sid,
            baseline=baseline,
            simulation=tobe_sim,
            comparison=comparison_result,
            prioritization=prioritization_result
        )
        
        summary_path = eval_dir / f"evaluation_summary_{sid}.json"
        summary_path.write_text(
            json.dumps(evaluation_summary, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        # Record summary
        self.sdl.record_artefact(
            sid=sid,
            phase="evaluation",
            artefact_type="evaluation_summary",
            path=summary_path,
            summary={
                "score": comparison_result.get("score"),
                "recommendation": comparison_result.get("recommendation", {}).get("type"),
                "prioritization_status": prioritization_result.get("status") if prioritization_result else "not_run",
                "overall_status": "complete"
            }
        )
        
        logger.info(f"Evaluation completed for session {sid}")
        
        result = {
            # Paths
            "baseline_path": str(base_path),
            "sim_path": str(sim_path),
            "comparison_report_path": str(comparison_report_path) if comparison_report_path else None,
            "summary_path": str(summary_path),
            "tobe_bpmn": str(to_be),

            # Key metrics
            "deltas": comparison_result.get("time_improvements", {}),
            "score": comparison_result.get("score"),
            "recommendation": comparison_result.get("recommendation", {}),
            "on_time_p90": tobe_sim.get("on_time_p90"),

            # Status
            "status": "success",
            "phase_complete": True,

            # New: flag if this was the first evaluation
            "is_first_evaluation": (eval_count == 0),
        }

        # Add prioritization results if available
        if prioritization_result and prioritization_result.get("status") == "success":
            result["prioritization"] = {
                "priority_backlog_path": prioritization_result.get("priority_backlog_path"),
                "executive_summary_path": prioritization_result.get("executive_summary_path"),
                "agent_report_path": prioritization_result.get("agent_evaluation_report_path"),
                "recommended_scenario": prioritization_result.get("recommended_scenario"),
                "measure_count": prioritization_result.get("measure_count")
            }
        else:
            result["prioritization"] = prioritization_result
        
        # 8. Send completion event to orchestrator
        if self.orchestrator:
            # Use eval_count we already calculated
            is_first = (eval_count == 0)
            event = {
                "type": "evaluation.done",
                "session_id": sid,
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "payload": {
                    "score": comparison_result.get("score"),
                    "recommendation": comparison_result.get("recommendation", {}),
                    "is_first_evaluation": is_first,  # This is critical!
                    "status": "success"
                },
                "artefacts": {
                    "baseline_path": str(base_path),
                    "sim_path": str(sim_path),
                    "comparison_report_path": str(comparison_report_path) if comparison_report_path else None,
                    "summary_path": str(summary_path),
                    "tobe_bpmn": str(to_be)
                }
            }
            
            # Save event for audit
            event_path = eval_dir / f"evaluation.done_{sid}.json"
            event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
            
            logger.info(f"Sending evaluation.done event to orchestrator (is_first={is_first})")
            next_cmd = self.orchestrator.handle_event(event)
            if next_cmd:
                logger.info(f"Orchestrator routing to: {next_cmd['target']}.{next_cmd['action']}")

            # Save versioned metrics for comparison
            version = 1
            state_path = self.sdl.get_session_dir(sid) / "control" / "state.json"
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                version = state.get("generation_iteration", 1)

            # Copy metrics with version
            versioned_base = eval_dir / f"baseline_metrics_v{version}.json"
            versioned_sim = eval_dir / f"sim_metrics_v{version}.json"
            versioned_comp = eval_dir / f"comparison_v{version}.json"

            import shutil
            shutil.copy(base_path, versioned_base)
            shutil.copy(sim_path, versioned_sim)
            if comparison_report_path:
                shutil.copy(comparison_report_path, versioned_comp)
        
        return result
    
    def _load_resource_config(self, path: Optional[Path]) -> Optional[Dict[str, Any]]:
        """Load resource configuration if available"""
        if not path or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load resource config: {e}")
            return None
    
    def _create_evaluation_summary(
        self,
        sid: str,
        baseline: Dict[str, Any],
        simulation: Dict[str, Any],
        comparison: Dict[str, Any],
        prioritization: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create comprehensive evaluation summary"""
        
        summary = {
            "sid": sid,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "phase": "evaluation",
            
            # Baseline summary
            "baseline": {
                "cycle_mean_s": baseline.get("global", {}).get("cycle_mean_s"),
                "cycle_p90_s": baseline.get("global", {}).get("cycle_p90_s"),
                "activities": len(baseline.get("per_activity", {})),
                "traces": baseline.get("global", {}).get("traces_total", 0)
            },
            
            # Simulation summary
            "simulation": {
                "cycle_mean_s": simulation.get("cycle_mean_s"),
                "cycle_p90_s": simulation.get("cycle_p90_s"),
                "on_time_p90": simulation.get("on_time_p90"),
                "cost_per_case": simulation.get("cost_per_case")
            },
            
            # Comparison summary
            "comparison": {
                "score": comparison.get("score"),
                "recommendation": comparison.get("recommendation", {}).get("type"),
                "improvements": {
                    "cycle_mean_pct": comparison.get("time_improvements", {}).get("cycle_mean_pct"),
                    "cycle_p90_pct": comparison.get("time_improvements", {}).get("cycle_p90_pct")
                },
                "sla_met": comparison.get("sla_compliance", {}).get("sla_met"),
                "risks": len(comparison.get("risks", [])),
                "benefits": len(comparison.get("benefits", []))
            },
            
            # Overall assessment
            "overall": {
                "ready_for_implementation": comparison.get("recommendation", {}).get("type") == "proceed_to_decision",
                "major_improvements": comparison.get("time_improvements", {}).get("cycle_mean_pct", 0) > 10,
                "has_risks": len(comparison.get("risks", [])) > 0,
                "quality_issues": len(comparison.get("structural_quality", {}).get("issues", []))
            }
        }
        
        # Add prioritization summary if available
        if prioritization and prioritization.get("status") == "success":
            summary["prioritization"] = {
                "status": "completed",
                "recommended_scenario": prioritization.get("recommended_scenario"),
                "measure_count": prioritization.get("measure_count"),
                "implementation_weeks": None  # Will be filled from agent report if available
            }
        elif prioritization:
            summary["prioritization"] = {
                "status": prioritization.get("status"),
                "reason": prioritization.get("reason") or prioritization.get("error")
            }
        
        return summary


# Convenience function for running evaluation
def run_evaluation(sid: str, sdl: MongoSDL, orchestrator=None, with_prioritization: bool = True) -> Dict[str, Any]:
    """
    Run complete evaluation phase
    
    Args:
        sid: Session ID
        sdl: MongoSDL instance
        orchestrator: Orchestrator instance for automatic routing (optional)
        with_prioritization: Whether to include prioritization agent (default: True)
        
    Returns:
        Dictionary with evaluation results
    """
    engine = EvaluationEngine(sdl, orchestrator=orchestrator)
    return engine.run(sid, run_prioritization=with_prioritization)