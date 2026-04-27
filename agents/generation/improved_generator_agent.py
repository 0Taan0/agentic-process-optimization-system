# agents/generation/improved_generator_agent.py
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)
import os
import json
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from enum import Enum
import xml.etree.ElementTree as ET

from core.mongo_sdl import MongoSDL
from openai import OpenAI

# Constants
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
N = {"bpmn": BPMN_NS}

class ProcessAction(Enum):
    """Allowed actions for process improvements"""
    MERGE_TASKS = "merge_tasks"
    PARALLELIZE_TASKS = "parallelize_tasks"
    REMOVE_GATEWAY = "remove_gateway"
    INSERT_EVENT = "insert_event"
    RENAME_TASK = "rename_task"
    REORDER = "reorder"
    CONSOLIDATE_REVIEWS = "consolidate_reviews"
    AUTOMATE_TASK = "automate_task"
    ADD_GATEWAY = "add_gateway"
    SPLIT_TASK = "split_task"
    ADD_MONITORING = "add_monitoring"
    OPTIMIZE_RESOURCE = "optimize_resource"

class OptimizationStrategy(Enum):
    """Available optimization strategies"""
    TIME = "time"
    COST = "cost"
    QUALITY = "quality"
    COMPLIANCE = "compliance"
    RESOURCE = "resource"
    AUTOMATION = "automation"

# Utilities
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

@dataclass
class ProcessElement:
    """Structured representation of a BPMN element"""
    id: str
    type: str
    name: str
    incoming: List[str] = None
    outgoing: List[str] = None
    
    def __post_init__(self):
        if self.incoming is None:
            self.incoming = []
        if self.outgoing is None:
            self.outgoing = []

@dataclass
class ImprovedProcessGeneratorConfig:
    strategy: OptimizationStrategy = OptimizationStrategy.TIME
    model: str = "gpt-5-mini"
    #temperature: float = 0.4  # 0.0-0.2: conservative, 0.3-0.7: creative, 0.8-1.0: very creative
    max_suggestions: int = 10
    enable_validation: bool = True
    prompts_dir: Optional[Path] = None

class ImprovedProcessGenerator:
    """
    AI-powered generator agent for process improvements.
    
    This agent:
    1. Analyzes the AS-IS process (BPMN)
    2. Considers requirements and constraints
    3. Generates strategy-based improvement suggestions
    4. Validates suggestions against hard constraints
    5. Persists structured suggestions
    """
    
    def __init__(
        self, 
        sdl: MongoSDL, 
        config: Optional[ImprovedProcessGeneratorConfig] = None
    ):
        self.sdl = sdl
        self.cfg = config or ImprovedProcessGeneratorConfig()
        self.client = OpenAI()
        
    # ---------- Public API ----------
    def run(self, sid: str) -> Dict[str, Any]:
        """Main method: Generates improvement suggestions for a session"""
        try:
            # 1. Load inputs
            current_bpmn_path = self._find_current_bpmn(sid)
            requirements = self._load_requirements(sid)
            constraints = self._load_constraints(sid)
            
            # 2. Analyze BPMN
            process_data = self._analyze_bpmn(current_bpmn_path)
            
            # 3. Generate suggestions
            suggestions = self._generate_suggestions(
                sid=sid,
                process_data=process_data,
                requirements=requirements,
                constraints=constraints
            )
            
            # 4. Validate (if enabled)
            if self.cfg.enable_validation:
                suggestions = self._validate_suggestions(
                    suggestions=suggestions,
                    process_data=process_data,
                    constraints=constraints
                )
            
            # 5. Persist result
            result_path = self._save_results(sid, suggestions, process_data)
            
            return {
                "status": "success",
                "suggestions_path": str(result_path),
                "suggestions_count": len(suggestions),
                "strategy": self.cfg.strategy.value,
                "validation_enabled": self.cfg.enable_validation
            }
            
        except Exception as e:
            # Log error
            self.sdl.record_error(sid, "generation", str(e))
            return {
                "status": "error",
                "error": str(e),
                "strategy": self.cfg.strategy.value
            }
    
    # ---------- BPMN Analysis ----------
    def _find_current_bpmn(self, sid: str) -> Path:
        """Finds the CURRENT BPMN - TO-BE if exists, else IST"""
        # FIRST: Check if we already have a TO-BE from previous generation
        gen_dir = self.sdl.get_session_dir(sid) / "generation"
        if gen_dir.exists():
            # Find latest TO-BE BPMN
            tobe_files = sorted(gen_dir.glob("tobe_bpmn_*.bpmn"), 
                            key=lambda p: p.stat().st_mtime, reverse=True)
            if tobe_files:
                logger.info(f"Using previous TO-BE as base: {tobe_files[0]}")
                return tobe_files[0]
        
        # FALLBACK: Use IST from interpretation
        interp_dir = self.sdl.get_session_dir(sid) / "interpretation"
        ist_files = list(interp_dir.glob("ist_bpmn_*.bpmn"))
        if not ist_files:
            raise FileNotFoundError(f"No BPMN file found")
        logger.info(f"Using IST as base: {ist_files[0]}")
        return ist_files[0]
    
    def _analyze_bpmn(self, bpmn_path: Path) -> Dict[str, Any]:
        """Analyzes BPMN and extracts structured data"""
        root = ET.parse(str(bpmn_path)).getroot()
        process = root.find(".//bpmn:process", N)
        
        if process is None:
            raise ValueError("No <process> element found in BPMN")
        
        elements: Dict[str, ProcessElement] = {}
        flows: List[Dict[str, str]] = []
        
        # Collect elements
        for elem in process:
            tag = self._get_tag_name(elem)
            elem_id = elem.get("id")
            
            if not elem_id:
                continue
                
            if tag == "sequenceFlow":
                flows.append({
                    "id": elem_id,
                    "source": elem.get("sourceRef", ""),
                    "target": elem.get("targetRef", "")
                })
            else:
                elements[elem_id] = ProcessElement(
                    id=elem_id,
                    type=tag,
                    name=elem.get("name", "")
                )
        
        # Add connections to elements
        for flow in flows:
            source_id = flow["source"]
            target_id = flow["target"]
            
            if source_id in elements:
                elements[source_id].outgoing.append(flow["id"])
            if target_id in elements:
                elements[target_id].incoming.append(flow["id"])
        
        return {
            "elements": elements,
            "flows": flows,
            "statistics": self._calculate_statistics(elements, flows)
        }
    
    def _calculate_statistics(self, elements: Dict[str, ProcessElement], flows: List[Dict]) -> Dict:
        """Calculates process statistics for better suggestions"""
        element_types = {}
        for elem in elements.values():
            element_types[elem.type] = element_types.get(elem.type, 0) + 1
        
        return {
            "total_elements": len(elements),
            "total_flows": len(flows),
            "element_types": element_types,
            "has_parallel_gateway": "parallelGateway" in element_types,
            "has_exclusive_gateway": "exclusiveGateway" in element_types,
            "task_count": element_types.get("task", 0) + element_types.get("userTask", 0) + 
                         element_types.get("serviceTask", 0) + element_types.get("scriptTask", 0)
        }
    
    # ---------- Suggestion Generation ----------
    def _generate_suggestions(
        self, 
        sid: str,
        process_data: Dict[str, Any],
        requirements: Dict[str, Any],
        constraints: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generates improvement suggestions with LLM"""
        
        # Create prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            process_data=process_data,
            requirements=requirements,
            constraints=constraints
        )
        
        # Call LLM
        response = self.client.chat.completions.create(
            model=self.cfg.model,
            #temperature=self.cfg.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        # Parse response
        content = response.choices[0].message.content
        parsed = json.loads(content)
        
        suggestions = parsed.get("suggestions", [])
        
        # Limit to max_suggestions
        if len(suggestions) > self.cfg.max_suggestions:
            suggestions = suggestions[:self.cfg.max_suggestions]
        
        # Add IDs and structure
        for i, suggestion in enumerate(suggestions):
            suggestion["id"] = f"sug_{sid}_{self.cfg.strategy.value}_{i+1}"
            suggestion["strategy"] = self.cfg.strategy.value
            c = suggestion.get("confidence")
            try:
                suggestion["confidence"] = float(c) if c is not None else 0.7
            except (TypeError, ValueError):
                suggestion["confidence"] = 0.7

        
        return suggestions
    
    def _build_system_prompt(self) -> str:
        """Creates the system prompt based on the optimization strategy"""
        strategy_prompts = {
            OptimizationStrategy.TIME: """You are a process optimization expert focused on time reduction.
Analyze BPMN processes and suggest improvements that reduce cycle time.
Focus on: parallelization, elimination of wait times, automation.""",
            
            OptimizationStrategy.COST: """You are a cost optimization expert for business processes.
Identify cost-intensive activities and suggest more efficient alternatives.
Focus on: resource optimization, automation of manual tasks, consolidation.""",
            
            OptimizationStrategy.QUALITY: """You are a quality improvement expert for processes.
Suggest improvements that increase process quality and consistency.
Focus on: control points, standardization, error prevention.""",
            
            OptimizationStrategy.COMPLIANCE: """You are a compliance expert for business processes.
Identify compliance risks and suggest compliant process improvements.
Focus on: documentation, approvals, audit trails.""",
            
            OptimizationStrategy.RESOURCE: """You are a resource management expert for processes.
Optimize resource utilization and avoid bottlenecks.
Focus on: load distribution, capacity planning, resource pooling.""",
            
            OptimizationStrategy.AUTOMATION: """You are an automation expert for business processes.
Identify automation potential and suggest technical solutions.
Focus on: RPA, system integration, AI support."""
        }
        
        base_prompt = strategy_prompts.get(self.cfg.strategy, strategy_prompts[OptimizationStrategy.TIME])
        
        return f"""{base_prompt}

IMPORTANT RULES:
1. Use ONLY element IDs that exist in the process
2. Suggest only structural changes (no layout changes)
3. Respect hard constraints and mark potential violations
4. Each suggestion must have a clear business justification
5. ALWAYS respond in JSON format
6. Use ONLY the provided 'goals' and 'soft_constraints' from the input; do NOT re-derive objectives.
7. Don't make up any information not present in the original prompt
8. Don't make up numbers. Only output numeric values if they are explicitly in the user prompt or existing artifacts (session_meta / requirements / constraints).

Allowed actions: {[action.value for action in ProcessAction]}"""
    
    def _build_user_prompt(
        self,
        process_data: Dict[str, Any],
        requirements: Dict[str, Any],
        constraints: Dict[str, Any]
    ) -> str:
        """Creates the user prompt with process data"""
        
        # Element list for whitelist
        elements_list = []
        for elem_id, elem in process_data["elements"].items():
            elements_list.append({
                "id": elem_id,
                "type": elem.type,
                "name": elem.name,
                "incoming": len(elem.incoming),
                "outgoing": len(elem.outgoing)
            })
        
        prompt_data = {
            "process_statistics": process_data["statistics"],
            "elements": elements_list[:50],  # Limit for token constraints
            "goals": requirements.get("goals", []),
            "soft_constraints": requirements.get("soft_constraints", []),
            "hard_constraints": constraints.get("hard_constraints", {}),
            "optimization_strategy": self.cfg.strategy.value,
            "output_format": {
                "suggestions": [
                    {
                        "action": "one of allowed actions",
                        "apply_to": ["element_id1", "element_id2"],
                        "reason": "business reason",
                        "expected_effect": {
                            "metric": "value change description"
                        },
                        "aligned_goals": ["goal1", "goal2"],
                        "confidence": 0.0  # 0.0 to 1.0
                    }
                ]
            }
        }
        
        return f"""Analyze the following BPMN process and generate improvement suggestions:

{json.dumps(prompt_data, indent=2, ensure_ascii=False)}

Generate {self.cfg.max_suggestions} concrete improvement suggestions in the specified JSON format."""
    
    # ---------- Validation ----------
    def _validate_suggestions(
        self,
        suggestions: List[Dict[str, Any]],
        process_data: Dict[str, Any],
        constraints: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Validates and annotates suggestions"""
        
        valid_element_ids = set(process_data["elements"].keys())
        hard_constraints = constraints.get("hard_constraints", {})
        
        validated_suggestions = []
        
        for suggestion in suggestions:
            validation_result = {
                "is_valid": True,
                "warnings": [],
                "errors": []
            }
            
            # 1. Check element IDs
            apply_to_ids = suggestion.get("apply_to", [])
            unknown_ids = [id for id in apply_to_ids if id not in valid_element_ids]
            
            if unknown_ids:
                validation_result["errors"].append(f"Unknown element IDs: {unknown_ids}")
                validation_result["is_valid"] = False
            
            # 2. Check action
            action = suggestion.get("action", "")
            if action not in [a.value for a in ProcessAction]:
                validation_result["errors"].append(f"Unknown action: {action}")
                validation_result["is_valid"] = False
            
            # 3. Check hard constraints
            if self._violates_hard_constraints(suggestion, process_data, hard_constraints):
                validation_result["warnings"].append("Potential violation of hard constraints")
            
            # 4. Calculate implementation effort
            suggestion["implementation_effort"] = self._estimate_effort(suggestion, process_data)
            
            # Add validation
            suggestion["validation"] = validation_result
            validated_suggestions.append(suggestion)
        
        # Sort by validity and confidence
        validated_suggestions.sort(
            key=lambda s: (s["validation"]["is_valid"], s.get("confidence", 0)),
            reverse=True
        )
        
        return validated_suggestions
    
    def _violates_hard_constraints(
        self,
        suggestion: Dict[str, Any],
        process_data: Dict[str, Any],
        hard_constraints: Dict[str, Any]
    ) -> bool:
        """Checks if a suggestion violates hard constraints"""
        
        action = suggestion.get("action", "")
        apply_to_ids = suggestion.get("apply_to", [])
        
        # Example: Start/End Events must not be removed
        if hard_constraints.get("must_have_start_event", True):
            for elem_id in apply_to_ids:
                elem = process_data["elements"].get(elem_id)
                if elem and elem.type == "startEvent" and action in ["remove_gateway", "merge_tasks"]:
                    return True
        
        if hard_constraints.get("must_have_end_event", True):
            for elem_id in apply_to_ids:
                elem = process_data["elements"].get(elem_id)
                if elem and elem.type == "endEvent" and action in ["remove_gateway", "merge_tasks"]:
                    return True
        
        return False
    
    def _estimate_effort(self, suggestion: Dict[str, Any], process_data: Dict[str, Any]) -> str:
        """Estimates the implementation effort"""
        action = suggestion.get("action", "")
        apply_to_count = len(suggestion.get("apply_to", []))
        
        # Simple heuristic
        if action in ["rename_task", "add_monitoring"]:
            return "low"
        elif action in ["merge_tasks", "split_task", "add_gateway"] and apply_to_count <= 2:
            return "medium"
        elif action in ["parallelize_tasks", "automate_task", "optimize_resource"]:
            return "high"
        else:
            return "medium"
    
    # ---------- Persistence ----------
    def _save_results(
        self,
        sid: str,
        suggestions: List[Dict[str, Any]],
        process_data: Dict[str, Any]
    ) -> Path:
        """Saves the results"""
        
        result = {
            "sid": sid,
            "strategy": self.cfg.strategy.value,
            "created_at": _now_iso(),
            "generator_config": {
                "model": self.cfg.model,
                #"temperature": self.cfg.temperature,
                "max_suggestions": self.cfg.max_suggestions
            },
            "process_statistics": process_data["statistics"],
            "suggestions": suggestions,
            "summary": {
                "total_suggestions": len(suggestions),
                "valid_suggestions": sum(1 for s in suggestions if s.get("validation", {}).get("is_valid", True)),
                "implementation_efforts": {
                    "low": sum(1 for s in suggestions if s.get("implementation_effort") == "low"),
                    "medium": sum(1 for s in suggestions if s.get("implementation_effort") == "medium"),
                    "high": sum(1 for s in suggestions if s.get("implementation_effort") == "high")
                }
            }
        }
        
        # Save file
        output_path = self.sdl.get_session_dir(sid) / "generation" / f"improvement_suggestions_{sid}_{self.cfg.strategy.value}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(output_path, json.dumps(result, indent=2, ensure_ascii=False))
        
        # Mirror in Mongo
        self.sdl.record_artefact(
            sid=sid,
            phase="generation",
            artefact_type="improvement_suggestions",
            path=output_path,
            summary=result["summary"],
            extra={"strategy": self.cfg.strategy.value}
        )
        
        return output_path
    
    # ---------- Helper Methods ----------
    def _get_tag_name(self, element: ET.Element) -> str:
        """Extracts the local tag name without namespace"""
        return element.tag.rsplit("}", 1)[-1] if "}" in element.tag else element.tag
    
    def _load_requirements(self, sid: str) -> Dict[str, Any]:
        """Loads requirements from the generation phase"""
        req_path = self.sdl.get_session_dir(sid) / "generation" / f"requirements_{sid}.json"
        if not req_path.exists():
            raise FileNotFoundError(f"Requirements not found: {req_path}")
        return json.loads(req_path.read_text(encoding="utf-8"))
    
    def _load_constraints(self, sid: str) -> Dict[str, Any]:
        """Loads constraints from the generation phase"""
        const_path = self.sdl.get_session_dir(sid) / "generation" / f"constraints_{sid}.json"
        if not const_path.exists():
            raise FileNotFoundError(f"Constraints not found: {const_path}")
        return json.loads(const_path.read_text(encoding="utf-8"))