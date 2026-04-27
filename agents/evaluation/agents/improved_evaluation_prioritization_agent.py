from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
import json
import re
# Send event
from control.gates import write_gate_event
from openai import OpenAI
from core.mongo_sdl import MongoSDL

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Risk levels for improvements"""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ImplementationScenario(Enum):
    """Implementation scenario types"""
    MINIMAL = "minimal"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass
class ImprovementMeasure:
    """Single improvement measure with scoring"""
    id: str
    action: str
    description: str
    target_elements: List[str]
    time_saving_pct: float
    effort_days: float
    risk_level: RiskLevel
    dependencies: List[str]
    wsjf_score: float
    rice_score: float
    rationale: str
    prerequisites: List[str]


@dataclass
class RiskMitigation:
    """Risk and its mitigation strategy"""
    risk: str
    impact: str
    likelihood: str
    mitigation: str
    residual_risk: str
    owner: str


class PrioritizationAgent:
    """Hybrid agent for process improvement prioritization and explanation"""
    
    def __init__(self, sdl: MongoSDL, llm_model: str = "gpt-5-mini"):
        self.sdl = sdl
        self.llm = OpenAI()
        self.llm_model = llm_model
        
    def evaluate_and_prioritize(
        self,
        sid: str,
        as_is_bpmn_path: Path,
        to_be_bpmn_path: Path,
        baseline_metrics_path: Path,
        sim_metrics_path: Path,
        comparison_report_path: Path,
        objectives_path: Optional[Path] = None,
        constraints_path: Optional[Path] = None,
        improvement_plan_path: Optional[Path] = None,
        resource_config_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Main evaluation and prioritization method
        """
        logger.info(f"Starting prioritization for session {sid}")
        
        # Load all inputs
        inputs = self._load_all_inputs(
            baseline_metrics_path,
            sim_metrics_path,
            comparison_report_path,
            objectives_path,
            constraints_path,
            improvement_plan_path,
            resource_config_path
        )
        
        # 1. Extract improvement measures
        measures = self._extract_improvement_measures(inputs)
        
        # 2. Calculate scores (deterministic)
        scored_measures = self._calculate_scores(measures, inputs)
        
        # 3. Generate explanations (LLM)
        explained_measures = self._generate_explanations(scored_measures, inputs)
        
        # 4. Identify and analyze risks (hybrid)
        risks = self._analyze_risks(explained_measures, inputs)
        
        # 5. Create implementation scenarios (hybrid)
        scenarios = self._create_scenarios(explained_measures, risks)
        
        # 6. Generate overall recommendation (LLM)
        recommendation = self._generate_recommendation(
            explained_measures, risks, scenarios, inputs
        )
        
        # 7. Create executive summary (LLM)
        executive_summary = self._create_executive_summary(
            explained_measures, risks, scenarios, recommendation, inputs
        )
        
        # Save all outputs
        outputs = self._save_outputs(
            sid,
            explained_measures,
            risks,
            scenarios,
            recommendation,
            executive_summary
        )
        
        return outputs
    
    def _load_all_inputs(self, *paths) -> Dict[str, Any]:
        """Load all input files"""
        inputs = {}
        path_names = [
            "baseline_metrics", "sim_metrics", "comparison_report",
            "objectives", "constraints", "improvement_plan", "resource_config"
        ]
        
        for path, name in zip(paths, path_names):
            if path and path.exists():
                try:
                    inputs[name] = json.loads(path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"Failed to load {name}: {e}")
                    inputs[name] = None
            else:
                inputs[name] = None
        
        return inputs
    
    def _extract_improvement_measures(self, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract individual improvement measures from various sources"""
        measures = []
        
        # From improvement plan
        if inputs.get("improvement_plan"):
            for suggestion in inputs["improvement_plan"].get("selected", []):
                measures.append({
                    "action": suggestion.get("action"),
                    "target_elements": suggestion.get("apply_to", []),
                    "description": suggestion.get("reason", ""),
                    "expected_effect": suggestion.get("expected_effect", {}),
                    "source": "improvement_plan"
                })
        
        # From comparison report benefits
        if inputs.get("comparison_report"):
            # Extract implicit measures from achieved benefits
            for benefit in inputs["comparison_report"].get("benefits", []):
                if "automated" in benefit.lower():
                    # Try to extract what was automated
                    measures.append({
                        "action": "automation",
                        "description": benefit,
                        "source": "comparison_benefits"
                    })
        
        seen = set()
        unique_measures = []
        for i, measure in enumerate(measures):
            key = (measure.get("action"), tuple(measure.get("target_elements", [])))
            if key not in seen:
                seen.add(key)
                measure["id"] = f"IMPL-{i+1:03d}"
                unique_measures.append(measure)
        
        return unique_measures
    
    def _calculate_scores(
        self, 
        measures: List[Dict[str, Any]], 
        inputs: Dict[str, Any]
    ) -> List[ImprovementMeasure]:
        """Calculate WSJF and RICE scores deterministically"""
        scored_measures = []
        
        comparison = inputs.get("comparison_report", {})
        total_time_improvement = abs(comparison.get("time_improvements", {}).get("cycle_mean_pct", 0))
        
        for measure in measures:
            # Estimate impact based on action type
            time_impact = self._estimate_time_impact(measure, total_time_improvement)
            effort = self._estimate_effort(measure)
            risk = self._estimate_risk(measure)
            
            # WSJF = (Business Value + Time Criticality + Risk Reduction) / Job Size
            business_value = time_impact / 10  # Scale to 0-10
            time_criticality = 5  # Default medium
            risk_reduction = 3 if risk in [RiskLevel.LOW, RiskLevel.VERY_LOW] else 1
            job_size = effort / 10  # Scale effort to 1-10
            
            wsjf_score = (business_value + time_criticality + risk_reduction) / max(job_size, 1)
            
            # RICE = (Reach * Impact * Confidence * Effort) / Effort
            reach = 100  # Assume all processes affected
            impact = time_impact / 20  # Scale to 0-5
            confidence = 0.8 if risk in [RiskLevel.LOW, RiskLevel.VERY_LOW] else 0.5
            
            rice_score = (reach * impact * confidence) / max(effort, 1)
            
            scored_measures.append(ImprovementMeasure(
                id=measure["id"],
                action=measure.get("action", ""),
                description=measure.get("description", ""),
                target_elements=measure.get("target_elements", []),
                time_saving_pct=time_impact,
                effort_days=effort,
                risk_level=risk,
                dependencies=self._identify_dependencies(measure, measures),
                wsjf_score=round(wsjf_score, 2),
                rice_score=round(rice_score, 2),
                rationale="",  # Will be filled by LLM
                prerequisites=[]
            ))
        
        # Sort by WSJF score
        scored_measures.sort(key=lambda x: x.wsjf_score, reverse=True)
        
        return scored_measures
    
    def _estimate_time_impact(self, measure: Dict[str, Any], total_improvement: float) -> float:
        """Estimate time impact of a measure"""
        action = measure.get("action", "").lower()
        
        # Simple heuristics based on action type
        if "automat" in action:
            return total_improvement * 0.4  # Automation has high impact
        elif "parallel" in action:
            return total_improvement * 0.3
        elif "merge" in action or "consolidat" in action:
            return total_improvement * 0.2
        else:
            return total_improvement * 0.1
    
    def _estimate_effort(self, measure: Dict[str, Any]) -> float:
        """Estimate implementation effort in days"""
        action = measure.get("action", "").lower()
        
        if "automat" in action:
            return 20  # Automation takes time
        elif "parallel" in action:
            return 10  # Moderate effort
        elif "merge" in action:
            return 5   # Simple change
        else:
            return 8   # Default
    
    def _estimate_risk(self, measure: Dict[str, Any]) -> RiskLevel:
        """Estimate risk level"""
        action = measure.get("action", "").lower()
        
        if "merge" in action or "rename" in action:
            return RiskLevel.VERY_LOW
        elif "parallel" in action:
            return RiskLevel.LOW
        elif "automat" in action:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def _identify_dependencies(
        self, 
        measure: Dict[str, Any], 
        all_measures: List[Dict[str, Any]]
    ) -> List[str]:
        """Identify dependencies between measures"""
        dependencies = []
        
        # Simple rule: automation depends on process cleanup
        if "automat" in measure.get("action", "").lower():
            for other in all_measures:
                if other["id"] != measure["id"] and "merge" in other.get("action", "").lower():
                    # Check if they affect same elements
                    if set(measure.get("target_elements", [])) & set(other.get("target_elements", [])):
                        dependencies.append(other["id"])
        
        return dependencies
    
    def _generate_explanations(
        self, 
        measures: List[ImprovementMeasure], 
        inputs: Dict[str, Any]
    ) -> List[ImprovementMeasure]:
        """Generate explanations using LLM"""
        
        # Prepare context
        comparison = inputs.get("comparison_report", {})
        improvements = comparison.get("time_improvements", {})
        
        prompt = f"""As a process improvement expert, explain why these measures create value:

        Process Improvements Achieved:
        - Cycle time reduction: {improvements.get('cycle_mean_pct', 0):.1f}%
        - P90 improvement: {improvements.get('cycle_p90_pct', 0):.1f}%

        For each measure below, provide:
        1. A clear business rationale (2-3 sentences)
        2. Specific prerequisites for implementation
        3. Expected challenges

        Measures:
        {json.dumps([{
            'id': m.id,
            'action': m.action,
            'description': m.description,
            'time_impact': m.time_saving_pct,
            'effort': m.effort_days,
            'wsjf_score': m.wsjf_score
        } for m in measures[:5]], indent=2)}  # Top 5 measures

        Return JSON with structure:
        {{
            "explanations": {{
                "IMPL-001": {{
                    "rationale": "...",
                    "prerequisites": ["...", "..."],
                    "challenges": "..."
                }}
            }}
        }}"""
        
        response = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": "You are a JSON generator. Respond with ONLY valid minified JSON, no prose."},
                {"role": "user", "content": prompt}
            ],
            #vielelicht muss ich das löschen
            #temperature=0
        )
        content = response.choices[0].message.content
        try:
            data = json.loads(content)
        except Exception:
            # Fallback: JSON aus dem Text ziehen
            m = re.search(r"\{.*\}", content, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}

        # Use the parsed data with fallback handling
        explanations = data
        
        # Update measures with explanations
        for measure in measures:
            if measure.id in explanations.get("explanations", {}):
                exp = explanations["explanations"][measure.id]
                measure.rationale = exp.get("rationale", "")
                measure.prerequisites = exp.get("prerequisites", [])
        
        return measures
    
    def _analyze_risks(
        self, 
        measures: List[ImprovementMeasure], 
        inputs: Dict[str, Any]
    ) -> List[RiskMitigation]:
        """Analyze risks using hybrid approach"""
        
        # Deterministic risk identification
        base_risks = []
        
        # Risk from automation
        automation_measures = [m for m in measures if "automat" in m.action.lower()]
        if automation_measures:
            base_risks.append({
                "category": "automation",
                "measures": [m.id for m in automation_measures]
            })
        
        # Risk from high complexity
        if inputs.get("comparison_report", {}).get("structural_quality", {}).get("stats", {}).get("gateway_density", 0) > 0.3:
            base_risks.append({
                "category": "complexity",
                "measures": []
            })
        
        # LLM for detailed risk analysis
        prompt = f"""Analyze risks for this process transformation:

        Key Changes:
        {json.dumps([{
            'id': m.id,
            'action': m.action,
            'description': m.description
        } for m in measures[:5]], indent=2)}

        Identified Risk Categories:
        {json.dumps(base_risks, indent=2)}

        For each major risk, provide:
        1. Detailed risk description
        2. Business impact if realized
        3. Likelihood (low/medium/high)
        4. Specific mitigation strategy
        5. Residual risk after mitigation
        6. Suggested owner (role)

        Return JSON:
        {{
            "risks": [
                {{
                    "risk": "description",
                    "impact": "business impact",
                    "likelihood": "medium",
                    "mitigation": "specific strategy",
                    "residual_risk": "low",
                    "owner": "Process Owner"
                }}
            ]
        }}"""
                
        response = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": "Respond with ONLY valid minified JSON, no prose."},
                {"role": "user", "content": prompt}
            ]
        )
        content = response.choices[0].message.content
        try:
            risk_data = json.loads(content)
        except Exception:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if m:
                try:
                    risk_data = json.loads(m.group(0))
                except Exception:
                    risk_data = {}
            else:
                risk_data = {}

        risks = []
        for r in risk_data.get("risks", []):
            risks.append(RiskMitigation(
                risk=r.get("risk", ""),
                impact=r.get("impact", ""),
                likelihood=r.get("likelihood", ""),
                mitigation=r.get("mitigation", ""),
                residual_risk=r.get("residual_risk", ""),
                owner=r.get("owner", "")
            ))
        
        return risks
    
    def _create_scenarios(
        self, 
        measures: List[ImprovementMeasure], 
        risks: List[RiskMitigation]
    ) -> Dict[str, Dict[str, Any]]:
        """Create implementation scenarios"""
        
        scenarios = {}
        
        # Minimal: Quick wins only
        minimal_measures = [
            m for m in measures 
            if m.effort_days <= 10 and m.risk_level in [RiskLevel.VERY_LOW, RiskLevel.LOW]
        ][:3]
        
        scenarios[ImplementationScenario.MINIMAL.value] = {
            "measures": [m.id for m in minimal_measures],
            "total_effort_days": sum(m.effort_days for m in minimal_measures),
            "total_improvement_pct": sum(m.time_saving_pct for m in minimal_measures),
            "implementation_weeks": math.ceil(sum(m.effort_days for m in minimal_measures) / 5 / 2),  # 2 people
            "risk_level": "very_low",
            "description": "Quick wins with minimal risk and effort"
        }
        
        # Balanced: 80/20 approach
        balanced_measures = []
        cumulative_improvement = 0
        target_improvement = sum(m.time_saving_pct for m in measures) * 0.8
        
        for m in measures:
            if cumulative_improvement < target_improvement and m.risk_level != RiskLevel.HIGH:
                balanced_measures.append(m)
                cumulative_improvement += m.time_saving_pct
        
        scenarios[ImplementationScenario.BALANCED.value] = {
            "measures": [m.id for m in balanced_measures],
            "total_effort_days": sum(m.effort_days for m in balanced_measures),
            "total_improvement_pct": sum(m.time_saving_pct for m in balanced_measures),
            "implementation_weeks": math.ceil(sum(m.effort_days for m in balanced_measures) / 5 / 3),  # 3 people
            "risk_level": "medium",
            "description": "Balanced approach targeting 80% of benefits"
        }
        
        # Aggressive: All measures
        scenarios[ImplementationScenario.AGGRESSIVE.value] = {
            "measures": [m.id for m in measures],
            "total_effort_days": sum(m.effort_days for m in measures),
            "total_improvement_pct": sum(m.time_saving_pct for m in measures),
            "implementation_weeks": math.ceil(sum(m.effort_days for m in measures) / 5 / 5),  # 5 people
            "risk_level": "high",
            "description": "Full transformation with maximum impact"
        }
        
        return scenarios
    
    def _generate_recommendation(
        self,
        measures: List[ImprovementMeasure],
        risks: List[RiskMitigation],
        scenarios: Dict[str, Dict[str, Any]],
        inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate overall recommendation using LLM"""
        
        prompt = f"""Based on this process improvement analysis, provide a strategic recommendation:

        Key Metrics:
        - Current improvement: {inputs.get('comparison_report', {}).get('time_improvements', {}).get('cycle_mean_pct', 0):.1f}%
        - Score: {inputs.get('comparison_report', {}).get('score', 0):.0f}/100

        Implementation Scenarios:
        {json.dumps(scenarios, indent=2)}

        Top Risks:
        {json.dumps([{
            'risk': r.risk,
            'mitigation': r.mitigation
        } for r in risks[:3]], indent=2)}

        Provide:
        1. Recommended scenario and why
        2. Implementation approach (phased/big-bang)
        3. Key success factors
        4. Expected ROI timeframe

        Return JSON:
        {{
            "recommended_scenario": "minimal|balanced|aggressive",
            "rationale": "...",
            "implementation_approach": "...",
            "success_factors": ["...", "..."],
            "roi_timeframe": "..."
        }}"""
        
        response = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": "Respond with ONLY valid minified JSON, no prose."},
                {"role": "user", "content": prompt}
            ]
        )
        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except Exception:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            return json.loads(m.group(0)) if m else {}

    
    def _create_executive_summary(
        self,
        measures: List[ImprovementMeasure],
        risks: List[RiskMitigation],
        scenarios: Dict[str, Dict[str, Any]],
        recommendation: Dict[str, Any],
        inputs: Dict[str, Any]
    ) -> str:
        """Create executive summary using LLM"""
        
        comparison = inputs.get("comparison_report", {})
        
        prompt = f"""Create a 1-page executive summary for process improvement initiative:

        Current State:
        - Process: Credit application processing
        - Cycle time improvement achieved: {comparison.get('time_improvements', {}).get('cycle_mean_pct', 0):.1f}%
        - Quality score: {comparison.get('score', 0):.0f}/100

        Recommendation: {recommendation.get('recommended_scenario')} scenario
        - Implementation: {scenarios[recommendation['recommended_scenario']]['implementation_weeks']} weeks
        - Expected benefit: {scenarios[recommendation['recommended_scenario']]['total_improvement_pct']:.1f}% time reduction

        Top 3 Measures:
        {json.dumps([{
            'action': m.action,
            'impact': f"{m.time_saving_pct:.1f}%",
            'effort': f"{m.effort_days:.0f} days"
        } for m in measures[:3]], indent=2)}

        Write a compelling executive summary (300-400 words) that:
        1. Opens with the bottom line (time/cost savings)
        2. Highlights top 3 improvements with concrete benefits
        3. Addresses main risk with mitigation
        4. Ends with clear next steps
        5. Uses business language (not technical jargon)"""
        
        response = self.llm.chat.completions.create(
            model=self.llm_model,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.choices[0].message.content
    
    def _save_outputs(
        self,
        sid: str,
        measures: List[ImprovementMeasure],
        risks: List[RiskMitigation],
        scenarios: Dict[str, Dict[str, Any]],
        recommendation: Dict[str, Any],
        executive_summary: str
    ) -> Dict[str, Any]:
        """Save all outputs and send event"""
        
        eval_dir = self.sdl.get_session_dir(sid) / "evaluation"
        eval_dir.mkdir(parents=True, exist_ok=True)
        
        # Priority backlog
        backlog = {
            "sid": sid,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "measures": [
                {
                    "id": m.id,
                    "action": m.action,
                    "description": m.description,
                    "target_elements": m.target_elements,
                    "impact": {
                        "time_saving_pct": m.time_saving_pct,
                        "effort_days": m.effort_days,
                        "risk_level": m.risk_level.value
                    },
                    "scores": {
                        "wsjf": m.wsjf_score,
                        "rice": m.rice_score
                    },
                    "dependencies": m.dependencies,
                    "rationale": m.rationale,
                    "prerequisites": m.prerequisites
                }
                for m in measures
            ]
        }
        
        backlog_path = eval_dir / f"priority_backlog_{sid}.json"
        backlog_path.write_text(json.dumps(backlog, indent=2, ensure_ascii=False), encoding="utf-8")
        
        # Agent evaluation report
        agent_report = {
            "sid": sid,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "risks": [
                {
                    "risk": r.risk,
                    "impact": r.impact,
                    "likelihood": r.likelihood,
                    "mitigation": r.mitigation,
                    "residual_risk": r.residual_risk,
                    "owner": r.owner
                }
                for r in risks
            ],
            "scenarios": scenarios,
            "recommendation": recommendation
        }
        
        report_path = eval_dir / f"agent_evaluation_report_{sid}.json"
        report_path.write_text(json.dumps(agent_report, indent=2, ensure_ascii=False), encoding="utf-8")
        
        # Executive summary
        summary_path = eval_dir / f"executive_summary_{sid}.md"
        summary_path.write_text(executive_summary, encoding="utf-8")
        
        # Record in MongoDB
        self.sdl.record_artefact(
            sid=sid,
            phase="evaluation",
            artefact_type="priority_backlog",
            path=backlog_path,
            summary={"measure_count": len(measures), "top_wsjf": measures[0].wsjf_score if measures else 0}
        )
        
        self.sdl.record_artefact(
            sid=sid,
            phase="evaluation",
            artefact_type="agent_evaluation_report",
            path=report_path,
            summary={
                "risk_count": len(risks),
                "recommended_scenario": recommendation.get("recommended_scenario")
            }
        )
        
        write_gate_event(
            sdl=self.sdl,
            sid=sid,
            gate="prioritization",
            payload={
                "measure_count": len(measures),
                "recommended_scenario": recommendation.get("recommended_scenario"),
                "total_improvement_pct": scenarios[recommendation["recommended_scenario"]]["total_improvement_pct"],
                "implementation_weeks": scenarios[recommendation["recommended_scenario"]]["implementation_weeks"]
            },
            artefacts={
                "priority_backlog": str(backlog_path),
                "agent_evaluation_report": str(report_path),
                "executive_summary": str(summary_path)
            },
            event_type="prioritization.done"
        )
        
        return {
            "priority_backlog_path": str(backlog_path),
            "agent_evaluation_report_path": str(report_path),
            "executive_summary_path": str(summary_path),
            "measure_count": len(measures),
            "recommended_scenario": recommendation.get("recommended_scenario"),
            "status": "success"
        }