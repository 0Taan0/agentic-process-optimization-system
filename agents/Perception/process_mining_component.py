from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pm4py

logger = logging.getLogger(__name__)


class DiscoveryMethod(Enum):
    INDUCTIVE = "inductive"
    DFG = "dfg"


@dataclass
class DiscoveryConfig:
    method: DiscoveryMethod = DiscoveryMethod.INDUCTIVE
    noise_threshold: float = 0.20
    noise_grid: Tuple[float, ...] = (0.10, 0.20, 0.30)
    max_events: Optional[int] = None
    try_both_methods: bool = False
    compute_conformance: bool = True
    simplicity_weight: float = 0.15


class ProcessMiningComponent:
    """
    SINGLE RESPONSIBILITY: Discover process model from raw XES
    Does NOT handle BPMN DI - that's done by interpreter
    """

    def __init__(self, sdl, discovery_config: Optional[DiscoveryConfig] = None):
        self.sdl = sdl
        self.cfg = discovery_config or DiscoveryConfig()

    def run(self, sid: str) -> Dict[str, Any]:
        """Execute discovery for session"""
        logger.info(f"[PM] Starting discovery for sid={sid} | method={self.cfg.method.value}")

        xes_path = self._find_newest_xes(sid)
        if not xes_path:
            msg = "No .xes file found for discovery."
            logger.error(f"[PM] {msg}")
            return {"status": "error", "error": msg}

        try:
            log = self._load_log(xes_path, max_events=self.cfg.max_events)
        except Exception as e:
            logger.exception("[PM] Failed to load XES log")
            return {"status": "error", "error": f"Failed to load XES: {e}"}

        try:
            best = self._discover_best_model(log)
        except Exception as e:
            logger.exception("[PM] Discovery failed")
            return {"status": "error", "error": f"Discovery failed: {e}"}

        try:
            perf = self._extract_performance_data(log)
        except Exception as e:
            logger.warning(f"[PM] Performance extraction failed: {e}")
            perf = {
                "num_cases": None,
                "num_events": None,
                "num_activities": None,
                "variants": None,
            }

        try:
            result = self._save_discovered_process(
                sid=sid,
                bpmn_model=best["bpmn"],
                performance_data=perf,
                discovery_meta=best["meta"],
            )
        except Exception as e:
            logger.exception("[PM] Persisting artefacts failed")
            return {"status": "error", "error": f"Persisting artefacts failed: {e}"}

        logger.info(f"[PM] Discovery complete for sid={sid} | method={best['meta'].get('method')}")
        return result

    def _discover_best_model(self, log):
        """Discover model(s) and pick best by conformance"""
        candidates: List[Dict[str, Any]] = []

        def add_candidate(bpmn_obj, meta):
            score = self._score_model(log, bpmn_obj, meta) if self.cfg.compute_conformance else 0.0
            nodes, edges = self._bpmn_complexity(bpmn_obj)
            if self.cfg.simplicity_weight > 0:
                penalty = self.cfg.simplicity_weight * math.log1p(nodes + edges)
                score = score - penalty
            candidates.append({"bpmn": bpmn_obj, "meta": meta, "score": score})

        if self.cfg.try_both_methods:
            for nz in (self.cfg.noise_grid or (self.cfg.noise_threshold,)):
                bpmn = self._discover_inductive(log, nz)
                add_candidate(bpmn, {"method": "inductive", "noise_threshold": nz})

            bpmn = self._discover_dfg(log)
            add_candidate(bpmn, {"method": "dfg"})
        else:
            if self.cfg.method == DiscoveryMethod.INDUCTIVE:
                grid = self.cfg.noise_grid or (self.cfg.noise_threshold,)
                for nz in grid:
                    bpmn = self._discover_inductive(log, nz)
                    add_candidate(bpmn, {"method": "inductive", "noise_threshold": nz})
            elif self.cfg.method == DiscoveryMethod.DFG:
                bpmn = self._discover_dfg(log)
                add_candidate(bpmn, {"method": "dfg"})
            else:
                raise ValueError(f"Unknown discovery method: {self.cfg.method}")

        if not candidates:
            raise RuntimeError("No discovery candidates produced.")

        best = max(candidates, key=lambda c: c["score"])
        return best

    def _discover_inductive(self, log, noise_threshold: float):
        """Inductive miner with noise threshold"""
        logger.info(f"[PM] Inductive Miner | noise={noise_threshold}")
        try:
            bpmn_model = pm4py.discover_bpmn_inductive(log, noise_threshold=noise_threshold)
            return bpmn_model
        except AttributeError:
            # Fallback: Petri net then convert
            net, im, fm = pm4py.discover_petri_net_inductive(log, noise_threshold=noise_threshold)
            bpmn_model = pm4py.convert_to_bpmn(net, im, fm)
            return bpmn_model

    def _discover_dfg(self, log):
        """DFG to BPMN conversion"""
        logger.info("[PM] DFG → BPMN path")
        dfg = pm4py.discover_dfg(log)
        bpmn_model = pm4py.convert_to_bpmn(dfg)
        return bpmn_model

    def _score_model(self, log, bpmn_obj, meta: Dict[str, Any]) -> float:
        """Compute conformance score: fitness + precision"""
        try:
            net, im, fm = pm4py.convert_to_petri_net(bpmn_obj)

            fitness = self._fitness_token_replay(log, net, im, fm)
            precision = self._precision_etconformance(log, net, im, fm)

            score = 0.6 * fitness + 0.4 * precision
            logger.info(f"[PM] Score | method={meta.get('method')} "
                        f"noise={meta.get('noise_threshold')} fitness={fitness:.3f} "
                        f"precision={precision:.3f} score={score:.3f}")
            return float(score)
        except Exception as e:
            logger.warning(f"[PM] Conformance scoring failed: {e}")
            return -0.05

    @staticmethod
    def _fitness_token_replay(log, net, im, fm) -> float:
        try:
            from pm4py.algo.conformance.tokenreplay import algorithm as token_replay
            res = token_replay.apply(log, net, im, fm)
            if not res:
                return 0.0
            fit_values = [r.get("trace_fitness", 0.0) or 0.0 for r in res]
            return float(sum(fit_values) / max(len(fit_values), 1))
        except Exception:
            return 0.0

    @staticmethod
    def _precision_etconformance(log, net, im, fm) -> float:
        try:
            from pm4py.algo.evaluation.precision import algorithm as precision_evaluator
            prec = precision_evaluator.apply(log, net, im, fm, 
                                            variant=precision_evaluator.Variants.ETCONFORMANCE_TOKEN)
            if isinstance(prec, dict):
                prec = prec.get("precision", 0.0) or 0.0
            return float(prec)
        except Exception:
            return 0.0

    @staticmethod
    def _bpmn_complexity(bpmn_obj) -> Tuple[int, int]:
        """Count nodes and flows"""
        try:
            nodes = getattr(bpmn_obj, "get_nodes", None)
            flows = getattr(bpmn_obj, "get_flows", None)
            n = len(nodes()) if callable(nodes) else 0
            e = len(flows()) if callable(flows) else 0
            return n, e
        except Exception:
            return (0, 0)

    def _find_newest_xes(self, sid: str) -> Optional[Path]:
        """Find newest XES file"""
        base = Path(self.sdl.get_session_dir(sid))
        if not base.exists():
            return None
        xes_files = list(base.rglob("*.xes")) + list(base.rglob("*.xes.gz"))
        if not xes_files:
            return None
        try:
            xes_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            pass
        return xes_files[0]

    def _load_log(self, path: Path, max_events: Optional[int] = None):
        """Load XES log with optional downsampling"""
        log = pm4py.read_xes(str(path))
        if max_events and max_events > 0:
            for trace in log:
                if len(trace) > max_events:
                    del trace[max_events:]
        return log

    def _extract_performance_data(self, log) -> Dict[str, Any]:
        """Extract basic performance metrics from log"""
        from pm4py.statistics.traces.generic.log import case_statistics
        from pm4py.statistics.variants.log import get as variants_module
        from pm4py.statistics.attributes.log import get as attributes_get

        num_cases = len(log)
        num_events = sum(len(t) for t in log)
        
        all_acts = attributes_get.get_attribute_values(log, "concept:name")
        num_activities = len(all_acts) if isinstance(all_acts, dict) else None

        variants = variants_module.get_variants(log)
        num_variants = len(variants) if isinstance(variants, dict) else None

        durations = case_statistics.get_all_case_durations(log, parameters={
            case_statistics.Parameters.TIMESTAMP_KEY: "time:timestamp"
        })
        
        dur_stats = None
        if durations:
            durations_sorted = sorted(durations)
            def pct(p):
                k = (p/100) * (len(durations_sorted)-1)
                f = math.floor(k); c = math.ceil(k)
                if f == c:
                    return durations_sorted[int(k)]
                return durations_sorted[f] + (k - f) * (durations_sorted[c] - durations_sorted[f])
            
            dur_stats = {
                "min": float(durations_sorted[0]),
                "p50": float(pct(50)),
                "p90": float(pct(90)),
                "max": float(durations_sorted[-1]),
            }

        return {
            "num_cases": num_cases,
            "num_events": num_events,
            "num_activities": num_activities,
            "variants": num_variants,
            "case_duration_seconds": dur_stats,
        }

    def _save_discovered_process(
        self,
        sid: str,
        bpmn_model: Any,
        performance_data: Dict[str, Any],
        discovery_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Save BPMN WITHOUT DI (raw pm4py output)
        DI will be added by interpreter
        """
        session_dir = Path(self.sdl.get_session_dir(sid))
        session_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        bpmn_path = session_dir / f"ist_bpmn_{sid}_{ts}_raw.bpmn"
        perf_path = session_dir / f"performance_{sid}_{ts}.json"

        # IMPORTANT: Save raw BPMN (no DI manipulation here)
        try:
            pm4py.write_bpmn(bpmn_model, str(bpmn_path))
            logger.info(f"[PM] Saved raw BPMN (no DI): {bpmn_path}")
        except Exception as e:
            logger.warning(f"[PM] pm4py BPMN export failed ({e}); trying fallback")
            # Try object method
            xml = None
            try:
                to_xml = getattr(bpmn_model, "to_xml", None)
                if callable(to_xml):
                    xml = to_xml()
            except Exception:
                pass
            if not xml:
                raise
            bpmn_path.write_text(xml, encoding="utf-8")

        # Write performance stats
        with open(perf_path, "w", encoding="utf-8") as f:
            json.dump(performance_data, f, indent=2)

        meta = {
            "discovery": {
                "method": discovery_meta.get("method"),
                "noise_threshold": discovery_meta.get("noise_threshold"),
                "conformance_score": discovery_meta.get("score"),
            },
            "files": {
                "bpmn_xml": str(bpmn_path),
                "performance_json": str(perf_path),
            },
            "stats": {
                "activities": performance_data.get("num_activities"),
                "cases": performance_data.get("num_cases"),
                "events": performance_data.get("num_events"),
                "variants": performance_data.get("variants"),
            },
            "note": "Raw BPMN from pm4py - DI will be added by interpreter"
        }

        try:
            self.sdl.record_artefact(
                sid=sid,
                phase="perception",
                artefact_type="process_discovery_raw",
                path=str(bpmn_path),
                summary=meta,
            )
        except Exception as e:
            logger.warning(f"[PM] SDL record_artefact failed: {e}")

        return {
            "status": "success",
            "ist_bpmn_path": str(bpmn_path),
            "performance_data_path": str(perf_path),
            "statistics": meta["stats"],
            "discovery": meta["discovery"],
        }