from pathlib import Path
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.mongo_sdl import MongoSDL
from openai import OpenAI
from agents.generation.io import (
    path_requirements, 
    save_json_and_record,
    T_BUSINESS_RULES
)

client = OpenAI()

def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

class RuleExtractionAgent:
    """
    Extracts concrete business rules from user context and objectives.
    
    1. Reads the original user_prompt from session meta
    2. Reads the extracted goals and soft_constraints from ObjectiveAgent
    3. Generates concrete, measurable business rules
    4. Categorizes rules by type (performance, quality, compliance, etc.)
    5. Preserves the original context for traceability
    """
    
    def __init__(self, sdl: MongoSDL):
        self.sdl = sdl
    
    def run(self, sid: str) -> Dict[str, Any]:
        """
        Extract business rules from user context and objectives
        
        Args:
            sid: Session ID
            
        Returns:
            Dictionary with rules_path and structured rules
        """
        # 1. Load original user context
        meta = self.sdl.read_session_meta(sid)
        user_prompt = meta.get("user_prompt", "")
        
        # 2. Load objectives from ObjectiveAgent output
        req_path = path_requirements(sid, self.sdl)
        if not req_path.exists():
            # If ObjectiveAgent hasn't run yet, we can't proceed
            raise FileNotFoundError(f"Requirements not found: {req_path}")
        
        requirements = json.loads(req_path.read_text(encoding="utf-8"))
        goals = requirements.get("goals", [])
        soft_constraints = requirements.get("soft_constraints", [])
        
        # 3. Extract rules using LLM
        rules_data = self._extract_rules_with_llm(
            user_prompt=user_prompt,
            goals=goals,
            soft_constraints=soft_constraints
        )
        
        # 4. Structure the complete rules document
        business_rules = {
            "sid": sid,
            "created_at": _now_iso(),
            "original_context": user_prompt,
            "extracted_from": {
                "goals": goals,
                "soft_constraints": soft_constraints
            },
            "business_rules": rules_data.get("business_rules", []),
            "performance_targets": rules_data.get("performance_targets", {}),
            "quality_requirements": rules_data.get("quality_requirements", []),
            "compliance_rules": rules_data.get("compliance_rules", []),
            "optimization_priorities": rules_data.get("optimization_priorities", [])
        }
        
        # 5. Save to filesystem and MongoDB
        from agents.generation.io import path_business_rules
        rules_path = path_business_rules(sid, self.sdl)
        
        save_json_and_record(
            self.sdl,
            sid,
            T_BUSINESS_RULES,
            rules_path,
            business_rules,
            summary={
                "total_rules": len(business_rules["business_rules"]),
                "has_performance_targets": bool(business_rules["performance_targets"]),
                "has_quality_requirements": bool(business_rules["quality_requirements"]),
                "has_compliance_rules": bool(business_rules["compliance_rules"])
            },
            overwrite=True
        )
        
        return {
            "rules_path": str(rules_path),
            "rules": business_rules
        }
    
    def _extract_rules_with_llm(
        self, 
        user_prompt: str, 
        goals: List[str], 
        soft_constraints: List[Any]
    ) -> Dict[str, Any]:
        """
        Use LLM to extract concrete business rules from context
        """
        system_prompt = """You are a business analyst expert who extracts concrete, measurable business rules from user requirements.

Your task is to:
1. Analyze the user's original context/prompt
2. Consider the already extracted goals and constraints
3. Generate specific, actionable business rules
4. Categorize rules by type
5. Identify performance targets with specific metrics
6. Preserve all important context from the original prompt
7. Don't make up any information not present in the original prompt
8. Don't make up numbers. Only output numeric values if they are explicitly in the user prompt or existing artifacts (session_meta / requirements / constraints).

Output structured JSON with:
- business_rules: Array of specific rules with id, type, rule text, and metrics
- performance_targets: Object with specific metric targets
- quality_requirements: Array of quality-related rules
- compliance_rules: Array of compliance/regulatory rules
- optimization_priorities: Ranked list of what to optimize first

Make rules SPECIFIC and MEASURABLE. Don't lose any context from the original prompt."""

        user_message = f"""Extract business rules from this context:

ORIGINAL USER PROMPT:
{user_prompt}

ALREADY EXTRACTED GOALS:
{json.dumps(goals, indent=2)}

ALREADY EXTRACTED SOFT CONSTRAINTS:
{json.dumps(soft_constraints, indent=2)}

Generate comprehensive business rules that capture ALL requirements from the original context.
Ensure no information is lost. Make rules specific and measurable."""

        response = client.chat.completions.create(
            model="gpt-5-mini",
            #temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        parsed = json.loads(content)
        
        # Ensure all required fields exist
        return {
            "business_rules": self._ensure_rule_structure(parsed.get("business_rules", [])),
            "performance_targets": parsed.get("performance_targets", {}),
            "quality_requirements": parsed.get("quality_requirements", []),
            "compliance_rules": parsed.get("compliance_rules", []),
            "optimization_priorities": parsed.get("optimization_priorities", [])
        }
    
    def _ensure_rule_structure(self, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Ensure each rule has proper structure
        """
        structured_rules = []
        
        for i, rule in enumerate(rules):
            if isinstance(rule, str):
                # Convert simple string to structured rule
                structured_rule = {
                    "id": f"BR{i+1:03d}",
                    "type": "general",
                    "rule": rule,
                    "mandatory": True
                }
            else:
                # Ensure rule has all required fields
                structured_rule = {
                    "id": rule.get("id", f"BR{i+1:03d}"),
                    "type": rule.get("type", "general"),
                    "rule": rule.get("rule", ""),
                    "mandatory": rule.get("mandatory", True)
                }
                
                # Add optional fields if present
                if "current_baseline" in rule:
                    structured_rule["current_baseline"] = rule["current_baseline"]
                if "target" in rule:
                    structured_rule["target"] = rule["target"]
                if "metric" in rule:
                    structured_rule["metric"] = rule["metric"]
                if "threshold" in rule:
                    structured_rule["threshold"] = rule["threshold"]
            
            if structured_rule["rule"]:  # Only add non-empty rules
                structured_rules.append(structured_rule)
        
        return structured_rules