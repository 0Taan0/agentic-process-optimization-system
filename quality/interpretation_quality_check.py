# agents/interpretation_quality_check.py
"""
As-Is Quality Check (ohne Dummy-KPIs)
- Prüft nur die formale/strukturelle Qualität des As-Is-BPMN.
- Conformance/KPIs werden NICHT berechnet/gespeichert, bis echte Algorithmen integriert sind.
"""

from pathlib import Path
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

class InterpretationQualityAgent:
    def __init__(self, sdl):
        self.sdl = sdl

    # ---------------------------
    # Helpers
    # ---------------------------
    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _check_bpmn_validity(self, bpmn_path: Path) -> Dict[str, Any]:
        """
        Prüft: XML wohlgeformt, Start/End vorhanden, mind. ein Task.
        Keine inhaltliche BPMN-Schema- oder Conformance-Prüfung.
        """
        try:
            tree = ET.parse(bpmn_path)
            root = tree.getroot()
        except ET.ParseError as e:
            return {
                "valid": False,
                "issues": [f"XML parse error: {e}"],
                "counts": {"start_events": 0, "end_events": 0, "tasks": 0}
            }

        ns = {"bpmn2": "http://www.omg.org/spec/BPMN/20100524/MODEL"}

        issues: List[str] = []
        start_events = root.findall(".//bpmn2:startEvent", ns)
        end_events   = root.findall(".//bpmn2:endEvent", ns)
        tasks        = root.findall(".//bpmn2:task", ns)

        if not start_events:
            issues.append("No start event found.")
        if not end_events:
            issues.append("No end event found.")
        if not tasks:
            issues.append("No task found.")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "counts": {
                "start_events": len(start_events),
                "end_events": len(end_events),
                "tasks": len(tasks),
            }
        }

    # ---------------------------
    # Public API
    # ---------------------------
    def run(self, sid: str, ist_bpmn_path: str, clean_xes_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Führt NUR den Quality-Check aus.
        Conformance/KPIs werden bewusst NICHT berechnet (kein Dummy).
        """
        ist_bpmn_file = Path(ist_bpmn_path)

        # 1) Quality
        quality_result = self._check_bpmn_validity(ist_bpmn_file)

        # 2) FS persistieren (nur Quality)
        base_dir = self.sdl.get_session_dir(sid) / "interpretation"
        base_dir.mkdir(parents=True, exist_ok=True)
        quality_path = base_dir / "as_is_quality.json"
        with open(quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_result, f, ensure_ascii=False, indent=2)

        # 3) Mongo spiegeln (nur Quality)
        self.sdl.record_artefact(
            sid,
            phase="interpretation",
            artefact_type="as_is_quality",
            path=str(quality_path),
            summary=quality_result
        )

        # Hinweis-Flag, dass KPIs (noch) nicht berechnet wurden
        # → keine Datei/kein Artefakt für KPIs, bis echte Algorithmen existieren
        return {
            "as_is_quality": quality_result,
            "quality_path": str(quality_path),
            "kpi_baseline_computed": False
        }
