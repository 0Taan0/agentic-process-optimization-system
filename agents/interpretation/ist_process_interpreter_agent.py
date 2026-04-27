from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Optional
import json
import logging
from datetime import datetime, timezone
import pm4py

from interpretation.improved_bpmn_di import ensure_valid_bpmn_di

logger = logging.getLogger(__name__)

class ISTProcessInterpreterAgent:
    """
    Enhanced interpreter that:
    1. Takes BPMN from process mining component
    2. Adds YOUR custom DI layout
    3. Runs conformance checking
    4. Saves enhanced BPMN + conformance metrics
    """
    
    def __init__(self, sdl):
        self.sdl = sdl
        
    def run(self, sid: str) -> Dict[str, Any]:
        """Main execution - enhance discovered BPMN"""
        logger.info(f"Starting IST interpretation for session {sid}")
        
        out_dir = self._ensure_phase_dir(sid)
        
        # 1. Find BPMN from process mining
        pm_bpmn_path = self._find_discovered_bpmn(sid)
        if not pm_bpmn_path:
            raise FileNotFoundError(
                f"No discovered BPMN found from process mining for sid={sid}"
            )
        
        logger.info(f"Found process mining BPMN: {pm_bpmn_path}")
        
        # 2. Load and enhance with YOUR custom DI
        bpmn_xml = pm_bpmn_path.read_text(encoding="utf-8")
        logger.info("Applying custom DI layout...")
        bpmn_xml = ensure_valid_bpmn_di(bpmn_xml)
        
        # 3. Save enhanced version
        enhanced_path = out_dir / f"ist_bpmn_{sid}.bpmn"
        enhanced_path.write_text(bpmn_xml, encoding="utf-8")
        
        # 4. Run conformance checking
        logger.info("Running conformance checking...")
        conformance = self._check_conformance(sid, enhanced_path)
        
        # 5. Create metadata
        meta = {
            "sid": sid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "process_mining_enhanced",
            "version": "4.0_pm4py_custom_di",
            "parameters": {
                "base_discovery": "pm4py_inductive",
                "layout_engine": "custom_di",
                "conformance_checked": False
            },
            "conformance": conformance,
            "files": {
                "bpmn": str(enhanced_path),
                "source_bpmn": str(pm_bpmn_path)
            }
        }
        
        meta_path = out_dir / f"model_meta_{sid}.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        
        # 6. Record in MongoDB
        self._record_artefacts(sid, enhanced_path, meta_path, conformance)
        
        logger.info(f"IST interpretation completed: {enhanced_path}")
        logger.info(f"Conformance - Fitness: {conformance.get('fitness', 0):.2%}, "
                   f"Precision: {conformance.get('precision', 0):.2%}")
        
        return {
            "ist_bpmn_path": str(enhanced_path),
            "model_meta_path": str(meta_path),
            "conformance": conformance,
        }
    
    def _find_discovered_bpmn(self, sid: str) -> Optional[Path]:
        """Find BPMN file created by process mining component"""
        session_dir = self.sdl.get_session_dir(sid)

        candidates = list(session_dir.rglob("ist_bpmn_*.bpmn"))
        
        if not candidates:
            return None
        
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]
    
    def _check_conformance(self, sid: str, bpmn_path: Path) -> Dict[str, Any]:
        """
        Run conformance checking: fitness + precision
        """
        try:
            xes_path = self._find_xes_log(sid)
            if not xes_path:
                logger.warning("No XES log found for conformance")
                return {"error": "no_log", "fitness": None, "precision": None}
            

            log = pm4py.read_xes(str(xes_path))

            bpmn_model = pm4py.read_bpmn(str(bpmn_path))
            net, im, fm = pm4py.convert_to_petri_net(bpmn_model)
            from pm4py.algo.conformance.tokenreplay import algorithm as token_replay
            replay_result = token_replay.apply(log, net, im, fm)
            
            fitness_values = [r.get('trace_fitness', 0) for r in replay_result]
            avg_fitness = sum(fitness_values) / len(fitness_values) if fitness_values else 0
            

            from pm4py.algo.evaluation.precision import algorithm as precision_algo
            precision = precision_algo.apply(
                log, net, im, fm, 
                variant=precision_algo.Variants.ETCONFORMANCE_TOKEN
            )
            
            if isinstance(precision, dict):
                precision = precision.get("precision", 0)

            conformance_score = 0.6 * avg_fitness + 0.4 * precision
            
            return {
                "fitness": float(avg_fitness),
                "precision": float(precision),
                "conformance_score": float(conformance_score),
                "traces_checked": len(log),
                "log_path": str(xes_path)
            }
            
        except Exception as e:
            logger.exception(f"Conformance checking failed: {e}")
            return {
                "error": str(e),
                "fitness": None,
                "precision": None,
                "conformance_score": None
            }
    
    def _find_xes_log(self, sid: str) -> Optional[Path]:
        """Find XES event log for conformance"""
        perception_dir = self.sdl.get_session_dir(sid) / "perception"

        candidates = [
            perception_dir / f"clean_xes_{sid}.xes",
            perception_dir / "clean_xes.xes"
        ]
        
        for c in candidates:
            if c.exists():
                return c

        uploads_dir = perception_dir / "uploads"
        if uploads_dir.exists():
            xes_files = list(uploads_dir.glob("*.xes")) + list(uploads_dir.glob("*.xes.gz"))
            if xes_files:
                return xes_files[0]
        
        return None
    
    def _ensure_phase_dir(self, sid: str) -> Path:
        """Ensure interpretation directory exists"""
        base_dir = Path(self.sdl.get_session_dir(sid))
        out_dir = base_dir / "interpretation"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
    
    def _record_artefacts(self, sid: str, bpmn_path: Path, meta_path: Path, conformance: Dict):
        """Record artefacts in MongoDB"""
        try:
            if hasattr(self.sdl, "record_artefact"):
                self.sdl.record_artefact(
                    sid, "interpretation", "ist_bpmn_enhanced", bpmn_path,
                    summary={
                        "conformance_score": conformance.get("conformance_score"),
                        "fitness": conformance.get("fitness"),
                        "precision": conformance.get("precision")
                    }
                )
                
                self.sdl.record_artefact(
                    sid, "interpretation", "model_meta", meta_path,
                    summary=conformance
                )
        except Exception as e:
            logger.warning(f"Failed to record artefacts: {e}")