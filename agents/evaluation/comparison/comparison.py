from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from enum import Enum

from core.mongo_sdl import MongoSDL

logger = logging.getLogger(__name__)

# XML Namespaces
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


class RecommendationType(Enum):
    """Possible recommendations from comparison"""
    PROCEED = "proceed_to_decision"
    PROCEED_WITH_CAVEATS = "proceed_with_caveats"
    REITERATE = "re_iterate_generation"


@dataclass
class StructuralIssue:
    """Represents a structural quality issue"""
    type: str
    severity: str  # "error", "warning", "info"
    element_id: Optional[str]
    description: str


@dataclass
class ComparisonResult:
    """Complete comparison result"""
    time_improvements: Dict[str, float]
    sla_compliance: Dict[str, Any]
    cost_analysis: Optional[Dict[str, float]]
    structural_quality: Dict[str, Any]
    risks: List[str]
    benefits: List[str]
    score: float
    recommendation: RecommendationType
    rationale: str


class ProcessComparator:
    """Main comparison engine for process evaluation"""
    
    def __init__(self, sdl: MongoSDL):
        self.sdl = sdl
        
    def compare(
        self,
        sid: str,
        as_is_bpmn_path: Path,
        to_be_bpmn_path: Path,
        baseline_metrics_path: Path,
        sim_metrics_path: Path,
        objectives_path: Optional[Path] = None,
        constraints_path: Optional[Path] = None,
        resource_config_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Perform comprehensive comparison of AS-IS vs TO-BE processes
        """
        logger.info(f"Starting comparison for session {sid}")
        
        # Load all inputs
        baseline = self._load_json(baseline_metrics_path)
        sim_metrics = self._load_json(sim_metrics_path)
        objectives = self._load_json(objectives_path) if objectives_path else {}
        constraints = self._load_json(constraints_path) if constraints_path else {}
        resource_config = self._load_json(resource_config_path) if resource_config_path else {}
        
        # 1. Time improvements
        time_improvements = self._calculate_time_improvements(baseline, sim_metrics)
        
        # 2. SLA compliance
        sla_compliance = self._check_sla_compliance(sim_metrics, objectives)
        
        # 3. Cost analysis
        cost_analysis = self._analyze_costs(baseline, sim_metrics, resource_config)
        
        # 4. Structural quality
        structural_quality = self._check_structural_quality(to_be_bpmn_path)
        
        # 5. Constraint validation
        constraint_violations = self._validate_constraints(structural_quality, constraints)
        
        # 6. Risk and benefit analysis
        risks = self._identify_risks(
            time_improvements, 
            structural_quality, 
            constraint_violations,
            cost_analysis
        )
        benefits = self._identify_benefits(
            time_improvements,
            sla_compliance,
            cost_analysis,
            structural_quality
        )
        
        # 7. Calculate overall score
        score = self._calculate_score(
            time_improvements,
            sla_compliance,
            cost_analysis,
            structural_quality,
            constraint_violations
        )
        
        # 8. Generate recommendation
        recommendation, rationale = self._generate_recommendation(
            score,
            time_improvements,
            sla_compliance,
            structural_quality,
            constraint_violations,
            risks
        )
        
        # Build comparison result
        result = ComparisonResult(
            time_improvements=time_improvements,
            sla_compliance=sla_compliance,
            cost_analysis=cost_analysis,
            structural_quality=structural_quality,
            risks=risks,
            benefits=benefits,
            score=score,
            recommendation=recommendation,
            rationale=rationale
        )
        
        # Save report
        report = self._create_report(sid, result, as_is_bpmn_path, to_be_bpmn_path)
        report_path = self._save_report(sid, report)
        
        # Trigger event
        self._send_evaluation_done_event(sid, report_path, result)
        
        return report
    
    def _load_json(self, path: Optional[Path]) -> Dict[str, Any]:
        """Load JSON file safely"""
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
            return {}
        
    #Neu lsöchen
    def compare_versions(self, sid: str, v1: int, v2: int) -> Dict:
        """Compare two TO-BE versions"""
        eval_dir = self.sdl.get_session_dir(sid) / "evaluation"
        
        # Load both versions
        v1_sim = json.loads((eval_dir / f"sim_metrics_v{v1}.json").read_text())
        v2_sim = json.loads((eval_dir / f"sim_metrics_v{v2}.json").read_text())
        
        delta = {
            "v1_to_v2_improvement": {
                "cycle_mean": v1_sim.get("cycle_mean_s", 0) - v2_sim.get("cycle_mean_s", 0),
                "cycle_p90": v1_sim.get("cycle_p90_s", 0) - v2_sim.get("cycle_p90_s", 0)
            },
            "v1_to_v2_improvement_pct": {
                "cycle_mean": ((v1_sim.get("cycle_mean_s", 0) - v2_sim.get("cycle_mean_s", 0)) / 
                            v1_sim.get("cycle_mean_s", 1)) * 100 if v1_sim.get("cycle_mean_s", 0) > 0 else 0
            }
        }
        
        return delta
    def _calculate_time_improvements(
        self,
        baseline: Dict[str, Any],
        sim_metrics: Dict[str, Any]
    ) -> Dict[str, float]:
        """Calculate time improvements between baseline and simulation"""
        base_global = baseline.get("global", {})
        
        improvements = {}
        
        # Mean cycle time
        base_mean = base_global.get("cycle_mean_s", 0)
        sim_mean = sim_metrics.get("cycle_mean_s", 0)
        if base_mean > 0:
            improvements["cycle_mean_pct"] = ((base_mean - sim_mean) / base_mean) * 100
        
        # P50 cycle time
        base_p50 = base_global.get("cycle_p50_s", 0)
        sim_p50 = sim_metrics.get("cycle_p50_s", 0)
        if base_p50 > 0:
            improvements["cycle_p50_pct"] = ((base_p50 - sim_p50) / base_p50) * 100
        
        # P90 cycle time
        base_p90 = base_global.get("cycle_p90_s", 0)
        sim_p90 = sim_metrics.get("cycle_p90_s", 0)
        if base_p90 > 0:
            improvements["cycle_p90_pct"] = ((base_p90 - sim_p90) / base_p90) * 100
        
        # Absolute values
        improvements["cycle_mean_delta_s"] = sim_mean - base_mean
        improvements["cycle_p90_delta_s"] = sim_p90 - base_p90
        
        return improvements
    
    def _check_sla_compliance(
        self,
        sim_metrics: Dict[str, Any],
        objectives: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check SLA compliance"""
        result = {
            "has_sla": False,
            "sla_met": False,
            "on_time_rate": None,
            "details": {}
        }
        
        # Check for SLA in objectives
        sla_p90 = (
            objectives.get("sla_seconds_p90") or
            objectives.get("SLA_P90_seconds") or
            objectives.get("sla", {}).get("p90_seconds")
        )
        
        if sla_p90:
            result["has_sla"] = True
            result["sla_value_s"] = float(sla_p90)
            
            sim_p90 = sim_metrics.get("cycle_p90_s", 0)
            result["actual_p90_s"] = sim_p90
            result["sla_met"] = sim_p90 <= float(sla_p90)
            result["margin_s"] = float(sla_p90) - sim_p90
            
            # On-time rate from simulation
            result["on_time_rate"] = sim_metrics.get("on_time_p90", 0)
        
        return result
    
    def _analyze_costs(
        self,
        baseline: Dict[str, Any],
        sim_metrics: Dict[str, Any],
        resource_config: Dict[str, Any]
    ) -> Optional[Dict[str, float]]:
        """Analyze cost changes"""
        if not resource_config:
            return None
        
        # Simple cost model: could be extended
        base_cost = baseline.get("cost_per_case", 0)
        sim_cost = sim_metrics.get("cost_per_case", 0)
        
        if not sim_cost and resource_config:
            # Estimate based on time reduction and rates
            rates = resource_config.get("hourly_rates", {})
            avg_rate = sum(rates.values()) / len(rates) if rates else 50.0
            
            base_time_h = baseline.get("global", {}).get("cycle_mean_s", 0) / 3600
            sim_time_h = sim_metrics.get("cycle_mean_s", 0) / 3600
            
            base_cost = base_time_h * avg_rate
            sim_cost = sim_time_h * avg_rate
        
        return {
            "base_cost": base_cost,
            "sim_cost": sim_cost,
            "delta": sim_cost - base_cost,
            "delta_pct": ((sim_cost - base_cost) / base_cost * 100) if base_cost > 0 else 0
        }
    
    def _check_structural_quality(self, bpmn_path: Path) -> Dict[str, Any]:
        """Check BPMN structural quality"""
        issues: List[StructuralIssue] = []
        stats = {}
        
        try:
            tree = ET.parse(str(bpmn_path))
            root = tree.getroot()
            ns = {"bpmn": BPMN_NS, "xsi": XSI_NS}
            process = root.find(".//bpmn:process", ns)
            
            if not process:
                issues.append(StructuralIssue(
                    type="missing_process",
                    severity="error",
                    element_id=None,
                    description="No process element found"
                ))
                return {"valid": False, "issues": issues, "stats": stats}
            
            # Count elements
            tasks = process.findall(".//bpmn:task", ns) + \
                    process.findall(".//bpmn:userTask", ns) + \
                    process.findall(".//bpmn:serviceTask", ns) + \
                    process.findall(".//bpmn:scriptTask", ns)
            
            events = process.findall(".//bpmn:startEvent", ns) + \
                     process.findall(".//bpmn:endEvent", ns) + \
                     process.findall(".//bpmn:intermediateCatchEvent", ns) + \
                     process.findall(".//bpmn:intermediateThrowEvent", ns)
            
            gateways = process.findall(".//bpmn:exclusiveGateway", ns) + \
                       process.findall(".//bpmn:parallelGateway", ns) + \
                       process.findall(".//bpmn:inclusiveGateway", ns)
            
            flows = process.findall(".//bpmn:sequenceFlow", ns)
            
            stats = {
                "task_count": len(tasks),
                "event_count": len(events),
                "gateway_count": len(gateways),
                "flow_count": len(flows),
                "total_nodes": len(tasks) + len(events) + len(gateways)
            }
            
            # Calculate complexity metrics
            if stats["total_nodes"] > 0:
                stats["gateway_density"] = stats["gateway_count"] / stats["total_nodes"]
            else:
                stats["gateway_density"] = 0
            
            # Check for start/end events
            start_events = process.findall(".//bpmn:startEvent", ns)
            end_events = process.findall(".//bpmn:endEvent", ns)
            
            if not start_events:
                issues.append(StructuralIssue(
                    type="missing_start",
                    severity="error",
                    element_id=None,
                    description="No start event found"
                ))
            elif len(start_events) > 1:
                issues.append(StructuralIssue(
                    type="multiple_starts",
                    severity="warning",
                    element_id=None,
                    description=f"Multiple start events found: {len(start_events)}"
                ))
            
            if not end_events:
                issues.append(StructuralIssue(
                    type="missing_end",
                    severity="error",
                    element_id=None,
                    description="No end event found"
                ))
            
            # Check for disconnected elements
            self._check_connectivity(process, flows, issues)
            
            # Check for proper gateway usage
            self._check_gateways(process, flows, issues)
            
            # Check condition expressions
            self._check_conditions(process, flows, issues)
            
        except ET.ParseError as e:
            issues.append(StructuralIssue(
                type="parse_error",
                severity="error",
                element_id=None,
                description=f"XML parse error: {str(e)}"
            ))
        
        return {
            "valid": not any(i.severity == "error" for i in issues),
            "issues": [
                {
                    "type": i.type,
                    "severity": i.severity,
                    "element_id": i.element_id,
                    "description": i.description
                }
                for i in issues
            ],
            "stats": stats
        }
    
    def _check_connectivity(
        self,
        process: ET.Element,
        flows: List[ET.Element],
        issues: List[StructuralIssue]
    ):
        """Check for disconnected elements"""
        # Build connection map
        connected = set()
        for flow in flows:
            source = flow.get("sourceRef")
            target = flow.get("targetRef")
            if source:
                connected.add(source)
            if target:
                connected.add(target)
        
        # Check all elements
        for elem in process:
            elem_id = elem.get("id")
            if not elem_id:
                continue
            
            tag = elem.tag.split("}")[-1]
            if tag in ["sequenceFlow", "textAnnotation", "dataObject", "association"]:
                continue
            
            if elem_id not in connected:
                issues.append(StructuralIssue(
                    type="disconnected_element",
                    severity="error",
                    element_id=elem_id,
                    description=f"Element {elem_id} ({tag}) is not connected"
                ))
    
    def _check_gateways(
        self,
        process: ET.Element,
        flows: List[ET.Element],
        issues: List[StructuralIssue]
    ):
        """Check gateway usage"""
        ns = {"bpmn": BPMN_NS}
        
        # Count incoming/outgoing for each element
        incoming = {}
        outgoing = {}
        
        for flow in flows:
            source = flow.get("sourceRef")
            target = flow.get("targetRef")
            
            if source:
                outgoing[source] = outgoing.get(source, 0) + 1
            if target:
                incoming[target] = incoming.get(target, 0) + 1
        
        # Check gateways
        for gw_type in ["exclusiveGateway", "parallelGateway", "inclusiveGateway"]:
            for gw in process.findall(f".//bpmn:{gw_type}", ns):
                gw_id = gw.get("id")
                if not gw_id:
                    continue
                
                in_count = incoming.get(gw_id, 0)
                out_count = outgoing.get(gw_id, 0)
                
                # Check for proper split/join
                if in_count == 0 and out_count == 0:
                    issues.append(StructuralIssue(
                        type="isolated_gateway",
                        severity="error",
                        element_id=gw_id,
                        description=f"Gateway {gw_id} has no connections"
                    ))
                elif in_count > 1 and out_count > 1:
                    issues.append(StructuralIssue(
                        type="mixed_gateway",
                        severity="warning",
                        element_id=gw_id,
                        description=f"Gateway {gw_id} acts as both split and join"
                    ))
    
    def _check_conditions(
        self,
        process: ET.Element,
        flows: List[ET.Element],
        issues: List[StructuralIssue]
    ):
        """Check condition expressions on flows from XOR gateways"""
        ns = {"bpmn": BPMN_NS, "xsi": XSI_NS}
        
        # Find XOR gateways
        xor_gateways = set()
        for xor in process.findall(".//bpmn:exclusiveGateway", ns):
            xor_id = xor.get("id")
            if xor_id:
                xor_gateways.add(xor_id)
        
        # Check flows from XOR gateways
        for flow in flows:
            source = flow.get("sourceRef")
            if source not in xor_gateways:
                continue
            
            # Check for condition
            condition = flow.find(".//bpmn:conditionExpression", ns)
            if not condition:
                issues.append(StructuralIssue(
                    type="missing_condition",
                    severity="warning",
                    element_id=flow.get("id"),
                    description=f"Flow from XOR gateway {source} missing condition"
                ))
            else:
                # Check condition type
                cond_type = condition.get(f"{{{XSI_NS}}}type")
                if cond_type != "bpmn:tFormalExpression":
                    issues.append(StructuralIssue(
                        type="invalid_condition_type",
                        severity="warning",
                        element_id=flow.get("id"),
                        description=f"Condition should be tFormalExpression, not {cond_type}"
                    ))
    
    def _validate_constraints(
        self,
        structural_quality: Dict[str, Any],
        constraints: Dict[str, Any]
    ) -> List[str]:
        """Validate hard constraints"""
        violations = []
        
        hard_constraints = constraints.get("hard_constraints", {})
        
        # Must have start event
        if hard_constraints.get("must_have_start_event", True):
            if any(i["type"] == "missing_start" for i in structural_quality.get("issues", [])):
                violations.append("Missing required start event")
        
        # Must have end event
        if hard_constraints.get("must_have_end_event", True):
            if any(i["type"] == "missing_end" for i in structural_quality.get("issues", [])):
                violations.append("Missing required end event")
        
        # Check for forbidden patterns
        if "no_disconnected_elements" in hard_constraints:
            disconnected = [
                i for i in structural_quality.get("issues", [])
                if i["type"] == "disconnected_element"
            ]
            if disconnected:
                violations.append(f"Found {len(disconnected)} disconnected elements")
        
        return violations
    
    def _identify_risks(
        self,
        time_improvements: Dict[str, float],
        structural_quality: Dict[str, Any],
        constraint_violations: List[str],
        cost_analysis: Optional[Dict[str, float]]
    ) -> List[str]:
        """Identify risks in the TO-BE process"""
        risks = []
        
        # Structural risks
        errors = [i for i in structural_quality.get("issues", []) if i["severity"] == "error"]
        if errors:
            risks.append(f"Process has {len(errors)} structural errors that must be fixed")
        
        # Complexity risks
        stats = structural_quality.get("stats", {})
        if stats.get("gateway_density", 0) > 0.3:
            risks.append("High gateway density may lead to complex process flow and confusion")
        
        # Performance risks
        if time_improvements.get("cycle_p90_pct", 0) < 5:
            risks.append("Minimal time improvement (<5%) may not justify transformation effort")
        
        # Cost risks
        if cost_analysis and cost_analysis.get("delta_pct", 0) > 10:
            risks.append(f"Cost increase of {cost_analysis['delta_pct']:.1f}% may impact ROI")
        
        # Constraint violations
        if constraint_violations:
            risks.append(f"Violates {len(constraint_violations)} hard constraints")
        
        # Automation risks
        if stats.get("task_count", 0) > 0:
            # This is simplified - would need actual automation analysis
            risks.append("High automation may reduce flexibility and human oversight")
        
        return risks
    
    def _identify_benefits(
        self,
        time_improvements: Dict[str, float],
        sla_compliance: Dict[str, Any],
        cost_analysis: Optional[Dict[str, float]],
        structural_quality: Dict[str, Any]
    ) -> List[str]:
        """Identify benefits of the TO-BE process"""
        benefits = []
        
        # Time benefits
        mean_improvement = time_improvements.get("cycle_mean_pct", 0)
        p90_improvement = time_improvements.get("cycle_p90_pct", 0)
        
        if mean_improvement > 10:
            benefits.append(f"Average cycle time reduced by {mean_improvement:.1f}%")
        
        if p90_improvement > 10:
            benefits.append(f"90th percentile time improved by {p90_improvement:.1f}%")
        
        # SLA benefits
        if sla_compliance.get("sla_met") and sla_compliance.get("has_sla"):
            margin = sla_compliance.get("margin_s", 0)
            benefits.append(f"SLA target achieved with {margin:.0f}s margin")
        
        # Cost benefits
        if cost_analysis and cost_analysis.get("delta_pct", 0) < -5:
            benefits.append(f"Cost reduction of {abs(cost_analysis['delta_pct']):.1f}%")
        
        # Structure benefits
        if structural_quality.get("valid"):
            benefits.append("Process structure is valid and well-formed")
        
        return benefits
    
    def _calculate_score(
        self,
        time_improvements: Dict[str, float],
        sla_compliance: Dict[str, Any],
        cost_analysis: Optional[Dict[str, float]],
        structural_quality: Dict[str, Any],
        constraint_violations: List[str]
    ) -> float:
        """Calculate overall score (0-100)"""
        score = 50.0  # Base score
        
        # Time improvement (max 30 points)
        mean_imp = time_improvements.get("cycle_mean_pct", 0)
        p90_imp = time_improvements.get("cycle_p90_pct", 0)
        time_score = min(30, (mean_imp + p90_imp) / 2 * 1.5)
        score += time_score
        
        # SLA compliance (max 20 points)
        if sla_compliance.get("has_sla"):
            if sla_compliance.get("sla_met"):
                score += 20
            else:
                # Partial credit based on how close
                margin = sla_compliance.get("margin_s", -999)
                if margin > -60:  # Within 1 minute
                    score += 10
        
        # Cost analysis (max 10 points)
        if cost_analysis:
            cost_delta = cost_analysis.get("delta_pct", 0)
            if cost_delta <= 0:
                score += 10
            elif cost_delta < 10:
                score += 5
        
        # Structural quality (can lose up to 30 points)
        errors = len([i for i in structural_quality.get("issues", []) if i["severity"] == "error"])
        warnings = len([i for i in structural_quality.get("issues", []) if i["severity"] == "warning"])
        
        score -= errors * 10
        score -= warnings * 2
        
        # Constraint violations (can lose up to 20 points)
        score -= len(constraint_violations) * 10
        
        # Gateway complexity penalty
        gateway_density = structural_quality.get("stats", {}).get("gateway_density", 0)
        if gateway_density > 0.3:
            score -= 5
        
        # Ensure score is in valid range
        return max(0, min(100, score))
    
    def _generate_recommendation(
        self,
        score: float,
        time_improvements: Dict[str, float],
        sla_compliance: Dict[str, Any],
        structural_quality: Dict[str, Any],
        constraint_violations: List[str],
        risks: List[str]
    ) -> Tuple[RecommendationType, str]:
        """Generate recommendation and rationale"""
        
        # Check for blocking issues
        if constraint_violations:
            return (
                RecommendationType.REITERATE,
                f"Process violates {len(constraint_violations)} hard constraints. "
                "These must be resolved before proceeding."
            )
        
        errors = [i for i in structural_quality.get("issues", []) if i["severity"] == "error"]
        if errors:
            return (
                RecommendationType.REITERATE,
                f"Process has {len(errors)} structural errors that prevent proper execution. "
                "A new version must be generated."
            )
        
        # Check score thresholds
        if score >= 70:
            if risks:
                return (
                    RecommendationType.PROCEED_WITH_CAVEATS,
                    f"Process achieves good score ({score:.0f}/100) but has {len(risks)} identified risks. "
                    "Proceed with caution and risk mitigation plan."
                )
            else:
                return (
                    RecommendationType.PROCEED,
                    f"Process achieves excellent score ({score:.0f}/100) with significant improvements "
                    "and no major risks. Ready for implementation."
                )
        
        elif score >= 50:
            improvements_good = (
                time_improvements.get("cycle_mean_pct", 0) > 10 or
                time_improvements.get("cycle_p90_pct", 0) > 10
            )
            
            if improvements_good and not sla_compliance.get("has_sla", True):
                return (
                    RecommendationType.PROCEED_WITH_CAVEATS,
                    f"Process shows moderate improvements (score {score:.0f}/100). "
                    "Consider addressing identified issues during implementation."
                )
            else:
                return (
                    RecommendationType.REITERATE,
                    f"Process score ({score:.0f}/100) indicates room for improvement. "
                    "Consider generating alternatives with different optimization strategies."
                )
        
        else:
            return (
                RecommendationType.REITERATE,
                f"Low score ({score:.0f}/100) indicates the process needs significant revision. "
                "Minimal improvements achieved and/or too many quality issues."
            )
    
    def _create_report(
        self,
        sid: str,
        result: ComparisonResult,
        as_is_path: Path,
        to_be_path: Path
    ) -> Dict[str, Any]:
        """Create comprehensive comparison report"""
        return {
            "sid": sid,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "inputs": {
                "as_is_bpmn": str(as_is_path),
                "to_be_bpmn": str(to_be_path)
            },
            "time_improvements": result.time_improvements,
            "sla_compliance": result.sla_compliance,
            "cost_analysis": result.cost_analysis,
            "structural_quality": result.structural_quality,
            "risks": result.risks,
            "benefits": result.benefits,
            "score": result.score,
            "recommendation": {
                "type": result.recommendation.value,
                "rationale": result.rationale
            },
            "summary": {
                "improvements_achieved": len(result.benefits),
                "risks_identified": len(result.risks),
                "structural_issues": len(result.structural_quality.get("issues", [])),
                "ready_for_implementation": result.recommendation == RecommendationType.PROCEED
            }
        }
    
    def _save_report(self, sid: str, report: Dict[str, Any]) -> Path:
        """Save comparison report"""
        eval_dir = self.sdl.get_session_dir(sid) / "evaluation"
        eval_dir.mkdir(parents=True, exist_ok=True)
        
        report_path = eval_dir / f"comparison_report_{sid}.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        
        # Record in MongoDB
        self.sdl.record_artefact(
            sid=sid,
            phase="evaluation",
            artefact_type="comparison_report",
            path=report_path,
            summary={
                "score": report["score"],
                "recommendation": report["recommendation"]["type"],
                "risks": len(report["risks"]),
                "benefits": len(report["benefits"])
            }
        )
        
        return report_path
    def _create_delta_report(self, sid: str, result: ComparisonResult) -> Dict[str, Any]:
        """Create focused delta report"""
        delta_report = {
            "sid": sid,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "performance_deltas": {
                "cycle_time": {
                    "mean": {
                        "absolute_s": result.time_improvements.get("cycle_mean_delta_s", 0),
                        "percentage": result.time_improvements.get("cycle_mean_pct", 0)
                    },
                    "p90": {
                        "absolute_s": result.time_improvements.get("cycle_p90_delta_s", 0),
                        "percentage": result.time_improvements.get("cycle_p90_pct", 0)
                    }
                },
                "improved": result.time_improvements.get("cycle_mean_pct", 0) > 0
            },
            "cost_deltas": None,
            "compliance_deltas": {
                "sla_compliance": result.sla_compliance.get("sla_met", False) if result.sla_compliance.get("has_sla") else None,
                "on_time_rate": result.sla_compliance.get("on_time_rate"),
                "sla_margin_s": result.sla_compliance.get("margin_s")
            },
            "structural_deltas": {
                "gateway_density": result.structural_quality.get("stats", {}).get("gateway_density", 0),
                "errors_added": len([i for i in result.structural_quality.get("issues", []) if i["severity"] == "error"]),
                "warnings_added": len([i for i in result.structural_quality.get("issues", []) if i["severity"] == "warning"])
            }
        }
        
        # Add cost deltas if available
        if result.cost_analysis:
            delta_report["cost_deltas"] = {
                "absolute": result.cost_analysis.get("delta", 0),
                "percentage": result.cost_analysis.get("delta_pct", 0),
                "improved": result.cost_analysis.get("delta", 0) < 0
            }
        
        return delta_report
    
    def _save_delta_report(self, sid: str, delta_report: Dict[str, Any]) -> Path:
        """Save delta report"""
        eval_dir = self.sdl.get_session_dir(sid) / "evaluation"
        delta_path = eval_dir / f"delta_report_{sid}.json"
        
        delta_path.write_text(
            json.dumps(delta_report, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        
        # Record in MongoDB
        self.sdl.record_artefact(
            sid=sid,
            phase="evaluation",
            artefact_type="delta_report",
            path=delta_path,
            summary={
                "time_improved": delta_report["performance_deltas"]["improved"],
                "cost_improved": delta_report["cost_deltas"]["improved"] if delta_report["cost_deltas"] else None
            }
        )
        
        return delta_path
    
    def _send_evaluation_done_event(
        self,
        sid: str,
        report_path: Path,
        result: ComparisonResult
    ):
        """Send evaluation.done event to orchestrator"""
        from control.gates import write_gate_event
        
        artefacts = {
            "comparison_report": str(report_path),
            "recommendation": result.recommendation.value
        }
        
        # Check state for evaluation count
        state_path = self.sdl.get_session_dir(sid) / "control" / "state.json"
        is_first = True
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                is_first = int(state.get("evaluation_count", 0)) == 0
            except Exception as e:
                logger.warning(f"Could not read evaluation_count: {e}")
                is_first = True

        payload = {
            "score": result.score,
            "recommendation": result.recommendation.value,
            "is_first_evaluation": is_first,  # <-- Neues Feld
            "ready_for_decision": result.recommendation != RecommendationType.REITERATE,
            "improvements": {
                "cycle_time_pct": result.time_improvements.get("cycle_mean_pct", 0),
                "sla_met": result.sla_compliance.get("sla_met", False)
            }
        }

        
        gate_path, event_dict = write_gate_event(
            sdl=self.sdl,
            sid=sid,
            gate="evaluation",
            payload=payload,
            artefacts=artefacts,
            event_type="evaluation.done"
        )
        
        logger.info(f"Evaluation completed for session {sid}: {result.recommendation.value}")



