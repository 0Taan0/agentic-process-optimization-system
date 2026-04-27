# agents/generation/schemas.py
from typing import List, Dict, Optional
from pydantic import BaseModel

# 1) Requirements (User-Ziele, weiche Constraints)
class Requirements(BaseModel):
    sid: str
    goals: List[str]                # z.B. ["cost↓", "lead_time↓"]
    soft_constraints: List[str]     # z.B. ["maintain_quality"]
    created_at: str                 # ISO Timestamp

# 2) Constraints (vereinheitlicht)
class Constraints(BaseModel):
    sid: str
    hard_rules: List[str]           # später von Unternehmen, erstmal []
    soft_rules: List[str]           # meist == goals
    created_at: str

# 3) Improvement Suggestions (pro Generator)
class ImprovementSuggestion(BaseModel):
    sid: str
    origin_agent: str               # z.B. "GeneratorCost"
    suggestions: List[Dict]         # Liste von maschinenlesbaren Vorschlägen
    created_at: str

# 4) Improvement Plan (merged)
class ImprovementPlan(BaseModel):
    sid: str
    selected: List[Dict]            # Übernommene Vorschläge
    rejected: List[Dict]            # Abgelehnte Vorschläge
    rationale: str                  # Warum diese Auswahl
    created_at: str

# 5) To-Be Meta (Changelog, KPIs)
class ToBeMeta(BaseModel):
    sid: str
    based_on_model: str             # ist_bpmn Pfad oder Hash
    changes: List[Dict]             # z.B. [{"action": "merge", "tasks": ["T1","T2"]}]
    kpis: Dict[str, float]          # {"lead_time_delta": -0.15}
    hash_bpmn: str
    created_at: str

# 6) Evaluation Input (für nächste Phase)
class EvalInput(BaseModel):
    sid: str
    tobe_bpmn_path: str
    variant_paths: List[str]
    reports: List[str]              # Pfade zu Lint/Compliance/KPI
    params: Dict[str, str]          # evtl. Simulationsparameter
    created_at: str
