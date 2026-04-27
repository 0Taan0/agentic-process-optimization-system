# agents/generation/infra.py
from __future__ import annotations

import json
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from core.mongo_sdl import MongoSDL
from control.orchestrator import Orchestrator

# Import all generation agents
from agents.generation.objective_agent import ObjectiveAgent
from agents.generation.constraint_agent import ConstraintAgent
from agents.generation.coordinator_agent import ProcessGuidelineCoordinatorAgent
from agents.generation.improved_generator_agent import (
    ImprovedProcessGenerator,
    ImprovedProcessGeneratorConfig,
    OptimizationStrategy
)

from agents.generation.improved_modeling_agent import HybridModelingAgent

from agents.generation.rule_extraction_agent import RuleExtractionAgent
__all__ = ["HybridModelingAgent"]
# Set up logging
logger = logging.getLogger(__name__)

def _now_iso() -> str:
    """Get current timestamp in ISO format"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def _ensure_dir(path: Path) -> None:
    """Ensure directory exists"""
    path.mkdir(parents=True, exist_ok=True)

class GenerationPhaseRunner:
    """
    Orchestrates the entire generation phase:
    1. Extract objectives (ObjectiveAgent)
    2. Define constraints (ConstraintAgent)
    3. Coordinate guidelines (CoordinatorAgent)
    4. Generate improvement suggestions (ImprovedProcessGenerator - multiple strategies)
    5. Model the TO-BE process (ImprovedProcessModelingAgent)
    6. Send completion event to orchestrator
    """
    
    def __init__(self, sdl: MongoSDL, orchestrator: Optional[Orchestrator] = None):
        self.sdl = sdl
        self.orchestrator = orchestrator or Orchestrator()
        
    def run(
        self,
        sid: str,
        strategies: Optional[List[str]] = None,
        llm_model: str = "gpt-5-mini",
        #llm_temperature: float = 0.3,
        max_suggestions_per_strategy: int = 10,
        apply_validation: bool = True,
        add_auto_di: bool = True,
    ) -> Dict[str, Any]:
        """
        Run the complete generation phase
        
        Args:
            sid: Session ID
            strategies: List of optimization strategies to use
            llm_model: LLM model for generation
            llm_temperature: Temperature for LLM sampling
            max_suggestions_per_strategy: Max suggestions per generator
            apply_validation: Whether to validate suggestions
            add_auto_di: Whether to add BPMN diagram interchange
            
        Returns:
            Dictionary with paths to all generated artifacts
        """
        logger.info(f"Starting generation phase for session {sid}")
        
        # Default strategies
        if strategies is None:
            strategies = ["time", "cost", "quality"]
        
        # Ensure generation directory exists
        gen_dir = self.sdl.get_session_dir(sid) / "generation"
        _ensure_dir(gen_dir)
        
        # Track all artifacts
        artifacts = {
            "sid": sid,
            "phase": "generation",
            "started_at": _now_iso()
        }
        
        try:
            # 1. Extract objectives
            logger.info("Step 1: Extracting objectives")
            obj_agent = ObjectiveAgent(self.sdl)
            obj_result = obj_agent.run(sid)
            artifacts["requirements"] = obj_result["requirements_path"]
            
            # 2. Define initial constraints
            logger.info("Step 2: Defining constraints")
            const_agent = ConstraintAgent(self.sdl)
            const_result = const_agent.run(sid)
            artifacts["initial_constraints"] = const_result["constraints_path"]
            
            # 3. Coordinate guidelines (merge requirements + constraints)
            logger.info("Step 3: Coordinating process guidelines")
            coord_agent = ProcessGuidelineCoordinatorAgent(self.sdl)
            coord_result = coord_agent.run(sid)
            artifacts["consolidated_constraints"] = coord_result["constraints_path"]

            # 3.5 Extract business rules (NEW)
            logger.info("Step 3.5: Extracting business rules")
            rule_agent = RuleExtractionAgent(self.sdl)
            rule_result = rule_agent.run(sid)
            artifacts["business_rules"] = rule_result["rules_path"]
            
            # 4. Generate improvement suggestions for each strategy
            logger.info("Step 4: Generating improvement suggestions")
            suggestions_paths = []
            suggestion_counts = {}
            
            for strategy in strategies:
                logger.info(f"  - Generating suggestions for strategy: {strategy}")
                
                # Convert string to enum
                try:
                    strategy_enum = OptimizationStrategy(strategy)
                except ValueError:
                    logger.warning(f"Unknown strategy '{strategy}', skipping")
                    continue
                
                # Configure generator
                gen_config = ImprovedProcessGeneratorConfig(
                    strategy=strategy_enum,
                    model=llm_model,
                    #temperature=llm_temperature,
                    max_suggestions=max_suggestions_per_strategy,
                    enable_validation=apply_validation
                )
                
                # Run generator
                generator = ImprovedProcessGenerator(self.sdl, gen_config)
                gen_result = generator.run(sid)
                
                if gen_result["status"] == "success":
                    suggestions_paths.append(gen_result["suggestions_path"])
                    suggestion_counts[strategy] = gen_result["suggestions_count"]
                else:
                    logger.error(f"Generator failed for strategy {strategy}: {gen_result.get('error')}")
            
            artifacts["suggestions"] = suggestions_paths
            artifacts["suggestion_counts"] = suggestion_counts
            
            # 5. Model the TO-BE process
            logger.info("Step 5: Modeling TO-BE process with HybridModelingAgent")

            # Find BASE BPMN (TO-BE if exists, else IST)
            gen_dir = self.sdl.get_session_dir(sid) / "generation"
            base_bpmn_path = None

            # Check for existing TO-BE
            if gen_dir.exists():
                tobe_files = sorted(gen_dir.glob("tobe_bpmn_*.bpmn"),
                                key=lambda p: p.stat().st_mtime, reverse=True)
                if tobe_files:
                    base_bpmn_path = tobe_files[0]
                    logger.info(f"Using previous TO-BE as base: {base_bpmn_path}")

            # Fallback to IST
            if not base_bpmn_path:
                interp_dir = self.sdl.get_session_dir(sid) / "interpretation"
                ist_files = list(interp_dir.glob("ist_bpmn_*.bpmn"))
                if not ist_files:
                    raise FileNotFoundError(f"No BPMN file found")
                base_bpmn_path = ist_files[0]
                logger.info(f"Using IST as base: {base_bpmn_path}")


            # Collect all suggestions but LIMIT to top 5
            all_suggestions = []
            for path_str in suggestions_paths:
                path = Path(path_str)
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    suggestions_from_file = data.get("suggestions") or []

                    # Confidence einmalig normalisieren (kein nested def nötig)
                    for s in suggestions_from_file:
                        c = s.get("confidence")
                        try:
                            s["confidence"] = float(c) if c is not None else 0.0
                        except (TypeError, ValueError):
                            s["confidence"] = 0.0

                    # Jetzt sauber sortieren
                    suggestions_from_file.sort(key=lambda s: s["confidence"], reverse=True)
                    all_suggestions.extend(suggestions_from_file[:10])


            # Final limit across all strategies
            all_suggestions = all_suggestions[:30]

            # Build business context
            business_context = {
                "user_prompt": self.sdl.read_session_meta(sid).get("user_prompt", ""),
                "goals": [],
                "rules": []
            }

            # Add from artifacts if they exist
            if "requirements" in artifacts:
                req_path = Path(artifacts["requirements"])
                if req_path.exists():
                    req_data = json.loads(req_path.read_text())
                    business_context["goals"] = req_data.get("goals", [])

            if "business_rules" in artifacts:
                rules_path = Path(artifacts["business_rules"])
                if rules_path.exists():
                    rules_data = json.loads(rules_path.read_text())
                    business_context["rules"] = rules_data.get("business_rules", [])

            # Create and run modeler
            modeler = HybridModelingAgent(self.sdl)
            modeling_result = modeler.transform(
                sid=sid,
                ist_bpmn_path=base_bpmn_path,
                suggestions=all_suggestions,
                business_context=json.dumps(business_context, ensure_ascii=False)
            )

            if modeling_result["status"] == "success":
                artifacts["tobe_bpmn"] = modeling_result["tobe_bpmn_path"]
                artifacts["tobe_meta"] = modeling_result["tobe_meta_path"]
                artifacts["applied_count"] = modeling_result.get("applied", 0)
                artifacts["skipped_count"] = modeling_result.get("skipped", 0)
                artifacts["validation_warnings"] = modeling_result.get("validation_warnings", [])
            else:
                raise RuntimeError(f"Modeling failed: {modeling_result.get('error')}")
            
            # 6. Record completion
            artifacts["completed_at"] = _now_iso()
            artifacts["status"] = "success"
            
            # Save phase summary
            summary_path = gen_dir / f"generation_summary_{sid}.json"
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(artifacts, f, indent=2, ensure_ascii=False)
            
            # Record in MongoDB
            self.sdl.record_artefact(
                sid=sid,
                phase="generation",
                artefact_type="phase_summary",
                path=summary_path,
                summary={
                    "strategies": strategies,
                    "total_suggestions": sum(suggestion_counts.values()),
                    "applied_suggestions": artifacts.get("applied_count", 0),
                    "skipped_suggestions": artifacts.get("skipped_count", 0)
                }
            )
            
            # 7. Send completion event to orchestrator
            self._send_completion_event(sid, artifacts)
            
            logger.info(f"Generation phase completed successfully for session {sid}")
            return artifacts
            
        except Exception as e:
            logger.error(f"Generation phase failed for session {sid}: {str(e)}")
            
            # Record error
            artifacts["status"] = "error"
            artifacts["error"] = str(e)
            artifacts["failed_at"] = _now_iso()
            
            # Send failure event
            self._send_failure_event(sid, str(e))
            
            raise
    
    def _send_completion_event(self, sid: str, artifacts: Dict[str, Any]) -> None:
        """Send generation.done event to orchestrator"""
        event = {
            "type": "generation.done",
            "session_id": sid,
            "event_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "payload": {
                "status": "success",
                "phase": "generation",
                "summary": {
                    "strategies_used": list(artifacts.get("suggestion_counts", {}).keys()),
                    "total_suggestions": sum(artifacts.get("suggestion_counts", {}).values()),
                    "applied_suggestions": artifacts.get("applied_count", 0),
                    "validation_warnings": len(artifacts.get("validation_warnings", []))
                }
            },
            "artefacts": {
                "requirements": artifacts.get("requirements"),
                "constraints": artifacts.get("consolidated_constraints"),
                "suggestions": artifacts.get("suggestions", []),
                "tobe_bpmn": artifacts.get("tobe_bpmn"),
                "tobe_meta": artifacts.get("tobe_meta"),
                "phase_summary": str(self.sdl.get_session_dir(sid) / "generation" / f"generation_summary_{sid}.json")
            }
        }
        
        self.orchestrator.handle_event(event)
        logger.info(f"Sent generation.done event for session {sid}")
    
    def _send_failure_event(self, sid: str, error: str) -> None:
        """Send generation.failed event to orchestrator"""
        event = {
            "type": "generation.failed",
            "session_id": sid,
            "event_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "payload": {
                "status": "error",
                "phase": "generation",
                "error": error
            }
        }
        
        self.orchestrator.handle_event(event)
        logger.error(f"Sent generation.failed event for session {sid}")

# Convenience function for backwards compatibility
def run_generation(
    sid: str,
    sdl: MongoSDL,
    orchestrator: Optional[Orchestrator] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Run generation phase (convenience wrapper)
    
    Args:
        sid: Session ID
        sdl: MongoSDL instance
        orchestrator: Optional orchestrator instance
        **kwargs: Additional arguments passed to GenerationPhaseRunner.run()
    
    Returns:
        Dictionary with generation results
    """
    runner = GenerationPhaseRunner(sdl, orchestrator)
    return runner.run(sid, **kwargs)
