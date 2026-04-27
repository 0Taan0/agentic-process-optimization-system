"""
Hybrid Modeling Agent - Combines LLM intelligence with deterministic transformations
for reliable BPMN process improvement.
"""

from __future__ import annotations
import json
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import xml.etree.ElementTree as ET
from collections import defaultdict

from openai import OpenAI

try:
    from core.mongo_sdl import MongoSDL
except Exception as e:
    raise ImportError(
        f"[improved_modeling_agent] Failed to import 'core.mongo_sdl': {e}"
    )


# Configure logging
logger = logging.getLogger(__name__)

# XML Namespaces
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
NSMAP = {
    "bpmn": BPMN_NS,
    "bpmndi": BPMNDI_NS,
    "di": DI_NS,
    "dc": DC_NS,
    "xsi": XSI_NS,
}


class TransformationAction(Enum):
    """Supported atomic transformation actions"""

    CREATE_PARALLEL_GATEWAY = "create_parallel_gateway"
    MERGE_SEQUENTIAL_TASKS = "merge_sequential_tasks"
    INSERT_AUTOMATION_MARKER = "insert_automation_marker"
    ADD_MONITORING_POINT = "add_monitoring_point"
    REMOVE_ELEMENT = "remove_element"
    RENAME_ELEMENT = "rename_element"
    REDIRECT_FLOW = "redirect_flow"
    INSERT_EVENT = "insert_event"
    SPLIT_TASK = "split_task"
    AUTOMATE_TASK = "automate_task"
    ADD_MONITORING = "add_monitoring"


@dataclass
class TransformationStep:
    """Single transformation step in the plan"""

    action: str
    params: Dict[str, Any]
    order: int
    dependencies: List[str] = None


@dataclass
class BPMNElement:
    """Simplified BPMN element representation"""

    id: str
    type: str
    name: str
    incoming: List[str]
    outgoing: List[str]
    attributes: Dict[str, str]


class BPMNModel:
    """Simplified BPMN model for easier manipulation"""

    def __init__(self, xml_string: str):
        self.root = ET.fromstring(xml_string)
        self.ns = {"bpmn": BPMN_NS, "bpmndi": BPMNDI_NS}
        self.process = self.root.find(".//bpmn:process", self.ns)
        self.elements = {}
        self.flows = {}
        self._parse_elements()

    def _parse_elements(self):
        """Parse BPMN elements into internal representation"""
        # Parse flow elements
        for elem in self.process:
            tag = elem.tag.split("}")[-1]
            elem_id = elem.get("id")

            if tag == "sequenceFlow":
                self.flows[elem_id] = {
                    "source": elem.get("sourceRef"),
                    "target": elem.get("targetRef"),
                    "element": elem,
                }
            else:
                self.elements[elem_id] = BPMNElement(
                    id=elem_id,
                    type=tag,
                    name=elem.get("name", ""),
                    incoming=[],
                    outgoing=[],
                    attributes=dict(elem.attrib),
                )

        # Build connections
        for flow_id, flow in self.flows.items():
            if flow["source"] in self.elements:
                self.elements[flow["source"]].outgoing.append(flow_id)
            if flow["target"] in self.elements:
                self.elements[flow["target"]].incoming.append(flow_id)

    def to_xml(self) -> str:
        """Convert back to XML string"""
        return ET.tostring(self.root, encoding="unicode")

    def get_element(self, elem_id: str) -> Optional[ET.Element]:
        """Get XML element by ID"""
        for elem in self.process:
            if elem.get("id") == elem_id:
                return elem
        return None


class LLMPlanner:
    """Uses LLM to create transformation plans based on suggestions"""

    def __init__(self, llm_client: OpenAI):
        self.llm = llm_client

    def create_plan(
        self,
        bpmn_model: BPMNModel,
        suggestions: List[Dict[str, Any]],
        business_context: str,
    ) -> List[TransformationStep]:
        """Create detailed transformation plan using LLM"""

        # Extract current structure
        structure = self._extract_structure(bpmn_model)
        valid_ids   = list(structure["elements"].keys())
        valid_names = [structure["elements"][i]["name"] for i in valid_ids if structure["elements"][i]["name"]]
        prompt = f"""You are a BPMN transformation expert. Create a detailed transformation plan.

        STRICT RULES:
        - Use ONLY action names from this whitelist:
        ["create_parallel_gateway","create_exclusive_gateway",
        "merge_sequential_tasks","insert_automation_marker",
        "add_monitoring","rename_element","redirect_flow",
        "remove_element","insert_event"]
        - Use ONLY element IDs from this whitelist (never invent new IDs): {json.dumps(valid_ids)}
        - If you reference by name, it MUST be exactly one of: {json.dumps(valid_names)}
        - Never invent IDs like "Activity_#" or "Task_#" unless present in the ID whitelist.

        Current BPMN Structure:
        {json.dumps(structure, indent=2)}

        Business Context:
        {business_context}

        Improvement Suggestions:
        {json.dumps(suggestions, indent=2)}

        Create a step-by-step transformation plan that:
        1. Applies the suggestions in the correct order
        2. Handles dependencies between transformations
        3. Ensures the result is a valid BPMN

        Return ONLY valid JSON in this format:
        {{
        "steps": [
            {{
                "action": "create_parallel_gateway",
                "params": {{
                    "after_element": "Task_3",
                    "parallel_tasks": ["Task_4", "Task_11", "Task_12"],
                    "join_before": "Task_5"
                }},
                "order": 1,
                "dependencies": []
            }},
            {{
                "action": "merge_sequential_tasks",
                "params": {{
                    "task1": "Task_7",
                    "task2": "Task_8",
                    "merged_name": "Create and Send Offer"
                }},
                "order": 2,
                "dependencies": []
            }}
            DISALLOWED:
            - Any action name not in the whitelist
            - Any parameter names other than those shown in the examples above

        ]
        }}"""

        response = self.llm.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        plan_json = json.loads(response.choices[0].message.content)
        allowed = {
            "create_parallel_gateway","merge_sequential_tasks","insert_automation_marker",
            "add_monitoring_point","add_monitoring","split_task","automate_task",
            "remove_element","rename_element","redirect_flow","insert_event"
        }
        valid_ids = set(structure["elements"].keys())
        valid_names = {structure["elements"][i]["name"] for i in valid_ids if structure["elements"][i]["name"]}

        def _ref_ok(ref: str) -> bool:
            return (ref in valid_ids) or (ref in valid_names)

        filtered = []
        for s in plan_json.get("steps", []):
            act = s.get("action")
            if act not in allowed:
                continue  # drop unknown action
            params = s.get("params", {})

            # einfache Param-Checks je Action
            if act == "merge_sequential_tasks":
                t1, t2 = params.get("task1"), params.get("task2")
                if not (t1 and t2):
                    continue
                # Mindestens Grundcheck: existiert als ID oder Name im Modell?
                if not (_ref_ok(t1) or _ref_ok(t2)):
                    # trotzdem zulassen — später löst der Transformer Namen/Varianten auf
                    pass

            if act == "create_parallel_gateway":
                if not (params.get("after_element") and params.get("join_before") and params.get("parallel_tasks")):
                    continue

            filtered.append({
                "action": act,
                "params": params,
                "order": s.get("order", 999),
                "dependencies": s.get("dependencies", [])
            })

        # dann aus 'filtered' die TransformationStep-Objekte bauen
        # Aus dem bereits validierten/normalisierten 'filtered' die Steps bauen
        steps = [
            TransformationStep(
                action=x["action"],
                params=x.get("params", {}),
                order=x.get("order", 999),
                dependencies=x.get("dependencies", []),
            )
            for x in filtered
        ]

        # Sortieren und zurückgeben
        steps.sort(key=lambda s: s.order)
        return steps


    def _extract_structure(self, model: BPMNModel) -> Dict[str, Any]:
        """Extract simplified structure for LLM understanding"""
        structure = {"elements": {}, "flows": []}

        for elem_id, elem in model.elements.items():
            structure["elements"][elem_id] = {
                "type": elem.type,
                "name": elem.name,
                "incoming_count": len(elem.incoming),
                "outgoing_count": len(elem.outgoing),
            }

        for flow_id, flow in model.flows.items():
            structure["flows"].append({"from": flow["source"], "to": flow["target"]})

        return structure


class DeterministicTransformer:
    """Executes transformation plan with deterministic operations"""

    def __init__(self):
        self.id_counter = 0
    #Neu
    def _normalize_step(self, step):
        #Mappt LLM-/Suggestion-Synonyme auf die erwarteten Param-Namen ansosnten fehler wegen falscher deutung
        action = step.action
        params = dict(step.params or {})

        if action == "create_parallel_gateway":
            # aliases → kanonische Keys
            if "parallel_tasks" not in params and "branches" in params:
                params["parallel_tasks"] = params.pop("branches")
            if "after_element" not in params and "after" in params:
                params["after_element"] = params.pop("after")
            if "join_before" not in params and "join_target" in params:
                params["join_before"] = params.pop("join_target")
            # störende Zusätze einfach verwerfen
            params.pop("gateway_id", None)

        elif action == "merge_sequential_tasks":
            # Handle various parameter names for merge
            if "task1" not in params:
                params["task1"] = params.get("first_task") or params.get("source_task") or params.get("from_task")
            if "task2" not in params:
                params["task2"] = params.get("second_task") or params.get("target_task") or params.get("to_task")
            if "merged_name" not in params:
                params["merged_name"] = params.get("name") or params.get("new_name") or "Merged Task"

        elif action in ["insert_automation_marker", "automate_task"]:
            # Normalize to have task_id
            if "task_id" not in params or not params.get("task_id"):
                # akzeptiere diverse Aliasse
                cand = (
                    params.get("task_id")
                    or params.get("task")
                    or params.get("task1")
                    or params.get("target")
                    or (params.get("apply_to", [None])[0] if isinstance(params.get("apply_to"), list) else params.get("apply_to"))
                    or params.get("element_id")
                )
                params["task_id"] = cand

        elif action in ["add_monitoring_point", "add_monitoring"]:
            # Normalize to have task_ids list
            if "task_ids" not in params:
                if "task_id" in params:
                    params["task_ids"] = [params.pop("task_id")]
                elif "apply_to" in params:
                    params["task_ids"] = params.pop("apply_to") if isinstance(params["apply_to"], list) else [params.pop("apply_to")]
                elif "targets" in params:
                    params["task_ids"] = params.pop("targets")

        # (Platzhalter: falls du remove_sequence_flow implementierst)
        if action == "remove_sequence_flow":
            pass

        return type(step)(
            action=action,
            params=params,
            order=step.order,
            dependencies=step.dependencies,
        )
    #Neu
    def apply_plan(
            self, bpmn_model: BPMNModel, plan: List[TransformationStep]
        ) -> BPMNModel:
            """Apply transformation plan step by step"""

            for step in plan:
                try:
                    step = self._normalize_step(step)
                    logger.info(f"Applying {step.action} with params: {step.params}")

                    if step.action == "create_parallel_gateway":
                        self._create_parallel_structure(bpmn_model, **step.params)
                    elif step.action == "merge_sequential_tasks":
                        self._merge_tasks(bpmn_model, **step.params)
                    elif step.action == "insert_automation_marker":
                        # Extract task_id from various possible parameter names
                        task_id = (
                            step.params.get("task_id")
                            or step.params.get("task")
                            or step.params.get("task1")
                            or (step.params.get("apply_to", [None])[0] if isinstance(step.params.get("apply_to"), list) else step.params.get("apply_to"))
                            or step.params.get("target")
                            or step.params.get("element_id")
                        )

                        if task_id:
                            self._mark_as_automated(bpmn_model, task_id=task_id)
                        else:
                            logger.warning(f"insert_automation_marker: No task_id found in params: {step.params}")
                    elif step.action in ["add_monitoring_point", "add_monitoring"]:
                        self._add_monitoring(bpmn_model, **step.params)
                    elif step.action == "split_task":
                        logger.info("split_task not implemented yet, skipping")
                    elif step.action == "automate_task":
                        task_id = (
                            step.params.get("task_id") or
                            step.params.get("target") or
                            step.params.get("apply_to", [None])[0] if isinstance(step.params.get("apply_to"), list) else step.params.get("apply_to")
                        )
                        if task_id:
                            self._mark_as_automated(bpmn_model, task_id=task_id)
                    elif step.action == "insert_event":
                        logger.info("insert_event not implemented yet, skipping")
                    elif step.action == "remove_element":
                        logger.info("remove_element not implemented yet, skipping")
                    elif step.action == "rename_element":
                        logger.info("rename_element not implemented yet, skipping")
                    elif step.action == "redirect_flow":
                        logger.info("redirect_flow not implemented yet, skipping")
                    else:
                        logger.warning(f"Unknown action: {step.action}")

                except Exception as e:
                    logger.error(f"Failed to apply {step.action}: {str(e)}")
                    logger.error(f"Step params were: {step.params}")
                    continue

            # CRITICAL: Clean up flows after ALL transformations
            self._cleanup_flows(bpmn_model)

            return bpmn_model

    def _generate_id(self, prefix: str) -> str:
        """Generate unique ID"""
        self.id_counter += 1
        return f"{prefix}_{self.id_counter}"

    def _create_parallel_structure(
        self,
        model: BPMNModel,
        after_element: str,
        parallel_tasks: List[str],
        join_before: str,
    ):
        """Create a parallel (AND) fork/join around given tasks.

        - nutzt _resolve_id für robuste Referenzen
        - pflegt model.flows UND model.elements
        """
        # --- 0) Referenzen auflösen
        after_id = self._resolve_id(model, after_element)
        join_target = self._resolve_id(model, join_before)
        branch_ids = [self._resolve_id(model, t) for t in (parallel_tasks or [])]
        branch_ids = [b for b in branch_ids if b is not None]
        if not after_id or not join_target or not branch_ids:
            raise ValueError(
                f"create_parallel_gateway: unresolved refs "
                f"(after={after_element}, join_before={join_before}, branches={parallel_tasks})"
            )

        # --- 1) Gateways anlegen + registrieren
        fork_id = self._generate_id("ParallelGateway")
        join_id = self._generate_id("ParallelGateway")

        fork = ET.SubElement(model.process, f"{{{BPMN_NS}}}parallelGateway", id=fork_id, name="Parallel Fork")
        join = ET.SubElement(model.process, f"{{{BPMN_NS}}}parallelGateway", id=join_id, name="Parallel Join")

        model.elements[fork_id] = BPMNElement(
            id=fork_id, type="parallelGateway", name="Parallel Fork",
            incoming=[], outgoing=[], attributes=dict(fork.attrib)
        )
        model.elements[join_id] = BPMNElement(
            id=join_id, type="parallelGateway", name="Parallel Join",
            incoming=[], outgoing=[], attributes=dict(join.attrib)
        )

        # --- 2) Flow NACH after_id auf fork umlenken (erste ausgehende Kante)
        # --- 2) Flow NACH after_id auf fork umlenken (nur EINE Kante)
        redirected = False
        for fid, f in list(model.flows.items()):
            if f["source"] == after_id and not redirected:  # Only redirect ONE flow
                f["element"].set("targetRef", fork_id)
                f["target"] = fork_id
                redirected = True
                break  # IMPORTANT: Only redirect the first flow!
        #Neu
        '''redirected = False
        for fid, f in list(model.flows.items()):  # list() to avoid runtime modification issues
            if f["source"] == after_id:
                f["element"].set("targetRef", fork_id)
                f["target"] = fork_id
                redirected = True'''
        # DON'T BREAK - redirect ALL outgoing flows!
        if not redirected:
            # falls kein Flow existierte, erzeuge einen
            sf_id = self._generate_id("Flow")
            sf = ET.SubElement(model.process, f"{{{BPMN_NS}}}sequenceFlow",
                            id=sf_id, sourceRef=after_id, targetRef=fork_id)
            model.flows[sf_id] = {"source": after_id, "target": fork_id, "element": sf}

        # --- 3) Fork → Branch-Tasks (neue Flows anlegen + registrieren)
        for tid in branch_ids:
            sf_id = self._generate_id("Flow")
            sf = ET.SubElement(model.process, f"{{{BPMN_NS}}}sequenceFlow",
                            id=sf_id, sourceRef=fork_id, targetRef=tid)
            model.flows[sf_id] = {"source": fork_id, "target": tid, "element": sf}

        # --- 4) Branch-Tasks → Join umlenken (Cache + XML)
        for fid, f in list(model.flows.items()):
            if f["source"] in branch_ids:
                f["element"].set("targetRef", join_id)
                f["target"] = join_id

        # --- 5) Join → join_target (neuen Flow anlegen + registrieren)
        sf_id = self._generate_id("Flow")
        sf = ET.SubElement(model.process, f"{{{BPMN_NS}}}sequenceFlow",
                        id=sf_id, sourceRef=join_id, targetRef=join_target)
        model.flows[sf_id] = {"source": join_id, "target": join_target, "element": sf}

    def _resolve_id(self, model: BPMNModel, ref: str) -> Optional[str]:
        """Resolve a task reference that may be an exact ID, exact name, a suffixed variant,
        or a case-insensitive name. Returns the canonical element ID or None."""
        if not ref:
            return None
        r_raw = ref.strip()
        r_low = r_raw.lower()

        # 0) Exakte ID?
        if r_raw in model.elements:
            return r_raw

        # 1) Exakter Name?
        for eid, e in model.elements.items():
            if (e.name or "") == r_raw:
                return eid

        # 2) Case-insensitive Name
        for eid, e in model.elements.items():
            if (e.name or "").strip().lower() == r_low:
                return eid

        # 3) Varianten-Suffixe entfernen (z. B. Task_12_auto → Task_12)
        SUFFIXES = ("_auto", "_manual", "_monitoring", "_service")
        base = r_raw
        for suf in SUFFIXES:
            if base.endswith(suf):
                base = base[: -len(suf)]
                break  # nur ein Suffix erwarten

        # 3a) Nach ID- oder Namensgleichheit mit 'base' suchen
        if base in model.elements:
            return base
        for eid, e in model.elements.items():
            if (e.name or "") == base:
                return eid
        base_low = base.lower()
        for eid, e in model.elements.items():
            if (e.name or "").strip().lower() == base_low:
                return eid

        # 4) Nummernbasiertes Matching (Task_12_* → Task_12…)
        #    Greife auf IDs/Namen zurück, die mit base beginnen oder die Nummer enthalten.
        import re
        m = re.search(r"(Task[_\- ]?\d+)", base, flags=re.IGNORECASE)
        if m:
            token = m.group(1).lower()
            # a) ID startswith
            for eid in model.elements.keys():
                if eid.lower().startswith(token):
                    return eid
            # b) Name startswith
            for eid, e in model.elements.items():
                if (e.name or "").lower().startswith(token):
                    return eid

        # 5) Fuzzy startswith/contains (letzter Versuch, sehr konservativ)
        for eid, e in model.elements.items():
            en = (e.name or "").lower()
            if base_low and (en.startswith(base_low) or base_low in en):
                return eid

        return None


    def _merge_tasks(
        self,
        model: BPMNModel,
        task1: str,
        task2: str,
        merged_name: str = None,
        **kwargs,
    ):
        """Merge two sequential tasks (task1 -> task2) into a single task.
        - akzeptiert IDs ODER Namen (via _resolve_id)
        - leitet ALLE eingehenden/ausgehenden Flows von t1 UND t2 auf merged um
        - entfernt den direkten Flow t1->t2
        - hält model.flows konsistent und dedupliziert ggf. doppelte Kanten
        """
        # 1) Zielnamen bestimmen
        if not merged_name:
            merged_name = kwargs.get("merged_id", kwargs.get("name", "Merged Task"))

        # 2) Referenzen robust auflösen
        t1 = self._resolve_id(model, task1)
        t2 = self._resolve_id(model, task2)
        if t1 is None or t2 is None:
            raise ValueError(f"Tasks not found: {task1}, {task2}")

        elem1 = model.get_element(t1)
        elem2 = model.get_element(t2)
        if elem1 is None or elem2 is None:
            raise ValueError(f"Tasks not found: {task1}, {task2}")

        # 3) Merged-Task anlegen
        merged_id = self._generate_id("Task")
        merged = ET.SubElement(model.process, f"{{{BPMN_NS}}}task", id=merged_id, name=merged_name)
        try:
            model.elements[merged_id] = BPMNElement(id=merged_id, type="task", name=merged_name,
                                                    incoming=[], outgoing=[], attributes=dict(merged.attrib))
        except Exception:
            pass

        # 4) Alle Flows sammeln (kopie, da wir mutieren)
        flows_items = list(model.flows.items())

        # 4a) ALLE eingehenden Flows nach t1/t2 -> merged
        for fid, f in flows_items:
            if f["target"] in (t1, t2):
                f["element"].set("targetRef", merged_id)
                f["target"] = merged_id

        # 4b) ALLE ausgehenden Flows von t1/t2 -> merged (t1->t2 ausnehmen)
        for fid, f in flows_items:
            if f["source"] in (t1, t2):
                if f["source"] == t1 and f["target"] == t2:
                    continue  # den direkten Verbindungsflow löschen wir gleich
                f["element"].set("sourceRef", merged_id)
                f["source"] = merged_id

        # 5) Direkt-Flow t1 -> t2 entfernen (falls vorhanden) – SAFE
        for fid, f in list(model.flows.items()):
            if f["source"] == t1 and f["target"] == t2:
                try:
                    if f["element"] is not None:
                        model.process.remove(f["element"])
                except ValueError:
                    pass
                model.flows.pop(fid, None)

        # 6) Ggf. doppelte Flows (merged->X oder Y->merged) deduplizieren
        seen = set()
        for fid, f in list(model.flows.items()):
            key = (f["source"], f["target"])
            if key in seen:
                # Duplikat entfernen
                try:
                    if f["element"] is not None:
                        model.process.remove(f["element"])
                except ValueError:
                    pass
                model.flows.pop(fid, None)
            else:
                seen.add(key)

        # 7) Alte Tasks entfernen + Index aufrÃ¤umen
        try:
            model.process.remove(elem1)
        except ValueError:
            pass
        try:
            model.process.remove(elem2)
        except ValueError:
            pass
        try:
            model.elements.pop(t1, None)
            model.elements.pop(t2, None)
        except Exception:
            pass

        # 8) CRITICAL: Deduplicate flows after merge
        seen_flows = set()
        for fid in list(model.flows.keys()):
            f = model.flows[fid]
            flow_key = (f["source"], f["target"])
            if flow_key in seen_flows:
                # Remove duplicate
                try:
                    model.process.remove(f["element"])
                except:
                    pass
                del model.flows[fid]
            else:
                seen_flows.add(flow_key)

        return merged_id



    def _mark_as_automated(self, model: BPMNModel, task_id: str):
        """Mark task as automated service task (serviceTask, implementation=##WebService)"""
        # ID/Name robust auflösen
        t = self._resolve_id(model, task_id)
        if t is None:
            return
        elem = model.get_element(t)
        if elem is None:
            return

        # In serviceTask wandeln
        elem.tag = f"{{{BPMN_NS}}}serviceTask"
        elem.set("implementation", "##WebService")

        # Optional: Elements-Index aktualisieren
        try:
            if t in model.elements:
                info = model.elements[t]
                info.element = elem
                model.elements[t] = info
        except Exception:
            pass


    def _add_monitoring(
        self,
        model: BPMNModel,
        task_ids: List[str] = None,
        task_id: str = None,
        **kwargs,
    ):
        """Add monitoring annotations to one or more tasks."""
        # 1) Eingabe vereinheitlichen
        if task_id and not task_ids:
            task_ids = [task_id]
        elif not task_ids:
            task_ids = kwargs.get("apply_to", []) or []

        # 2) Für jede (ID/Name) aufgelöste Task eine Annotation setzen
        for ref in task_ids:
            t = self._resolve_id(model, ref)
            if t is None:
                continue
            elem = model.get_element(t)
            if elem is None:
                continue

            # TextAnnotation erzeugen
            annot_id = self._generate_id("TextAnnotation")
            annotation = ET.SubElement(model.process, f"{{{BPMN_NS}}}textAnnotation")
            annotation.set("id", annot_id)

            text = ET.SubElement(annotation, f"{{{BPMN_NS}}}text")
            text.text = "Monitor: SLA tracking enabled"

            # Association anlegen (Annotation → Task)
            assoc = ET.SubElement(model.process, f"{{{BPMN_NS}}}association")
            assoc.set("id", self._generate_id("Association"))
            assoc.set("sourceRef", annot_id)
            assoc.set("targetRef", t)

            # Optional: falls du einen Elements-Index führen willst
            try:
                model.elements[annot_id] = model.ElementInfo(id=annot_id, name=None, element=annotation)
            except Exception:
                pass

    def _cleanup_flows(self, model: BPMNModel):
        """Remove duplicate and self-referencing flows"""
        seen_flows = set()
        to_remove = []
        
        for fid, f in model.flows.items():
            # Remove self-loops
            if f["source"] == f["target"]:
                to_remove.append(fid)
                continue
                
            # Remove duplicates
            flow_key = (f["source"], f["target"])
            if flow_key in seen_flows:
                to_remove.append(fid)
            else:
                seen_flows.add(flow_key)
        
        # Remove marked flows
        for fid in to_remove:
            if fid in model.flows:
                try:
                    model.process.remove(model.flows[fid]["element"])
                except:
                    pass
                del model.flows[fid]



class LLMRefiner:
    """Refines the transformed BPMN using LLM"""

    def __init__(self, llm_client: OpenAI):
        self.llm = llm_client

    def refine(
        self,
        bpmn_model: BPMNModel,
        original_plan: List[TransformationStep],
        business_context: str,
        suggestions: List[Dict[str, Any]]
    ) -> str:
        """Refine and fix any issues in the transformed BPMN"""

        current_xml = bpmn_model.to_xml()

        prompt = f"""You are a BPMN 2.0 expert.
        Return ONLY a JSON object: {{"xml": "<FULL BPMN 2.0 XML HERE>"}}

        Hard requirements:
        - Use namespaces: bpmn, bpmndi, di, dc, xsi (xsi=http://www.w3.org/2001/XMLSchema-instance).
        - For any conditional flow, use:
        <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">${{...}}</bpmn:conditionExpression>
        - Do NOT place free text inside <bpmn:sequenceFlow>. No "Condition: ..." strings.
        - Include BPMN-DI (BPMNDiagram, BPMNPlane, BPMNShape, BPMNEdge with di:waypoint).
        - Return complete, parseable XML. No markdown fences, no comments.

        Context:
        {business_context}

        Improvement Suggestions:
        {json.dumps(suggestions, indent=2)}

        IMPORTANT: Ensure ALL suggestions are properly reflected in the BPMN:
        - Merge redundant tasks (like O_CANCELLED and A_CANCELLED into single "Process Cancelled")
        - Add parallel gateways where parallelization was suggested
        - Insert monitoring points and annotations as requested
        - REMOVE any disconnected elements or invalid constructs!
        - Ensure that you dont over complicate with mulitple lanes/pools if not needed
        - Dont create unnecessary intermediate events if not needed
        - BE sure that all events are connected properly with sequence flows and not dangling

        Current XML:
        {current_xml}
        """
        #        - make only slight changes to the structure
        #das wieder rein
        #Transformation Plan (what was attempted):
        #{json.dumps([{"action": s.action, "params": s.params} for s in original_plan], indent=2)}
        resp = self.llm.chat.completions.create(
            model="gpt-5",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )

        try:
            data = json.loads(resp.choices[0].message.content)
            refined_xml = data.get("xml") or current_xml
            # Quick validity check: falls Refiner ungültiges XML liefert → zurück zum aktuellen Modell
            try:
                ET.fromstring(refined_xml)
            except ET.ParseError as e:
                logger.warning(f"Refiner produced invalid XML, falling back to current model: {e}")
                refined_xml = current_xml

        except Exception:
            refined_xml = current_xml

        return refined_xml


class BPMNValidator:
    """Validates and auto-fixes common BPMN issues"""

    def validate(self, xml_string: str) -> Tuple[bool, List[str]]:
        """Validate BPMN and return issues"""
        issues = []

        try:
            root = ET.fromstring(xml_string)
            ns = {"bpmn": BPMN_NS}
            process = root.find(".//bpmn:process", ns)

            if process is None:
                issues.append("No process element found")
                return False, issues

            # Check for start/end events
            starts = process.findall(".//bpmn:startEvent", ns)
            ends = process.findall(".//bpmn:endEvent", ns)

            if not starts:
                issues.append("No start event")
            if not ends:
                issues.append("No end event")

            # Check for disconnected elements
            elements = {}
            flows = {}

            for elem in process:
                elem_id = elem.get("id")
                if elem_id:
                    tag = elem.tag.split("}")[-1]
                    if tag == "sequenceFlow":
                        flows[elem_id] = {
                            "source": elem.get("sourceRef"),
                            "target": elem.get("targetRef"),
                        }
                    else:
                        elements[elem_id] = {
                            "type": tag,
                            "incoming": [],
                            "outgoing": [],
                        }

            # Build connections
            for flow_id, flow in flows.items():
                if flow["source"] in elements:
                    elements[flow["source"]]["outgoing"].append(flow_id)
                if flow["target"] in elements:
                    elements[flow["target"]]["incoming"].append(flow_id)

            # Find disconnected elements
            for elem_id, elem in elements.items():
                if (
                    not elem["incoming"]
                    and not elem["outgoing"]
                    and elem["type"] not in ["textAnnotation", "dataObject"]
                ):
                    issues.append(f"Disconnected element: {elem_id}")

            return len(issues) == 0, issues

        except ET.ParseError as e:
            issues.append(f"XML Parse Error: {str(e)}")
            return False, issues

    def auto_fix(self, xml_string: str) -> str:
        """Attempt to fix common issues"""
        try:
            root = ET.fromstring(xml_string)
            ns = {"bpmn": BPMN_NS}
            process = root.find(".//bpmn:process", ns)

            # Remove disconnected gateways
            for elem in list(process):
                if elem.tag.endswith("Gateway"):
                    elem_id = elem.get("id")
                    # Check if it has any connections
                    has_connections = False
                    for flow in process.findall(".//bpmn:sequenceFlow", ns):
                        if (
                            flow.get("sourceRef") == elem_id
                            or flow.get("targetRef") == elem_id
                        ):
                            has_connections = True
                            break

                    if not has_connections:
                        process.remove(elem)
                        logger.info(f"Removed disconnected gateway: {elem_id}")

            return ET.tostring(root, encoding="unicode")

        except Exception as e:
            logger.error(f"Auto-fix failed: {str(e)}")
            return xml_string


class HybridModelingAgent:
    """Main hybrid modeling agent combining LLM and deterministic approaches"""

    def __init__(self, sdl: MongoSDL, config: Optional[Dict[str, Any]] = None):
        self.sdl = sdl
        self.config = config or {}

        # Initialize components
        self.llm_client = OpenAI()
        self.planner = LLMPlanner(self.llm_client)
        self.transformer = DeterministicTransformer()
        self.refiner = LLMRefiner(self.llm_client)
        self.validator = BPMNValidator()

    def transform(
        self,
        sid: str,
        ist_bpmn_path: Path,
        suggestions: List[Dict[str, Any]],
        business_context: str,
    ) -> Dict[str, Any]:
        """Main transformation method"""

        try:
            # Load IST-BPMN
            with open(ist_bpmn_path, "r", encoding="utf-8") as f:
                ist_xml = f.read()

            bpmn_model = BPMNModel(ist_xml)

            # Stage 1: LLM Planning
            logger.info("Stage 1: Creating transformation plan with LLM")
            transformation_plan = self.planner.create_plan(
                bpmn_model, suggestions, business_context
            )

            # Stage 2: Deterministic Execution
            logger.info("Stage 2: Applying transformations deterministically")
            transformed_model = self.transformer.apply_plan(
                bpmn_model, transformation_plan
            )

            # Stage 3: LLM Refinement
            logger.info("Stage 3: Refining with LLM")
            refined_xml = self.refiner.refine(
                transformed_model, transformation_plan, business_context, suggestions
            )
            refined_xml = self._sanitize_bpmn(refined_xml)
            refined_xml = self._normalize_events_and_flows(refined_xml)
            refined_xml = self._ensure_bpmndi(refined_xml)

            # Stage 4: Validation and Auto-fix
            logger.info("Stage 4: Validating and fixing")
            is_valid, issues = self.validator.validate(refined_xml)

            if not is_valid:
                logger.warning(f"Validation issues: {issues}")
                refined_xml = self.validator.auto_fix(refined_xml)
                is_valid, issues = self.validator.validate(refined_xml)

            # Save results
            output_paths = self._save_results(sid, refined_xml, transformation_plan)

            return {
                "status": "success",
                "tobe_bpmn_path": str(output_paths["tobe_bpmn_path"]),
                "tobe_meta_path": str(output_paths["tobe_meta_path"]),
                "applied": len(transformation_plan),
                "skipped": 0,
                "transformations_applied": len(transformation_plan),
                "validation_issues": issues,
                "validation_warnings": issues,
            }

        except Exception as e:
            logger.error(f"Transformation failed: {str(e)}")
            return {"status": "error", "error": str(e)}

    def _sanitize_bpmn(self, xml_string: str) -> str:
        """
        Wandelt Freitext-Bedingungen in <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">${...}</bpmn:conditionExpression> um
        und akzeptiert auch sFlow-Attribut 'condition'.
        """
        try:
            ET.register_namespace("bpmn", BPMN_NS)
            ET.register_namespace("bpmndi", BPMNDI_NS)
            ET.register_namespace("di", DI_NS)
            ET.register_namespace("dc", DC_NS)
            ET.register_namespace("xsi", XSI_NS)

            root = ET.fromstring(xml_string)
            ns = {"bpmn": BPMN_NS}

            for sf in root.findall(".//bpmn:sequenceFlow", ns):
                # schon korrekt?
                if sf.find("bpmn:conditionExpression", ns) is not None:
                    continue

                # Freitext?
                txt = (sf.text or "").strip()
                if txt:
                    cond = txt.replace("Condition:", "").replace("condition:", "").strip()
                    if not cond.startswith("${"):
                        cond = "${" + cond + "}"
                    ce = ET.SubElement(sf, f"{{{BPMN_NS}}}conditionExpression")
                    ce.set(f"{{{XSI_NS}}}type", "bpmn:tFormalExpression")
                    ce.text = cond
                    sf.text = None

                # Attribut?
                cond_attr = sf.attrib.pop("condition", None)
                if cond_attr:
                    cond = cond_attr.strip()
                    if not cond.startswith("${"):
                        cond = "${" + cond + "}"
                    ce = ET.SubElement(sf, f"{{{BPMN_NS}}}conditionExpression")
                    ce.set(f"{{{XSI_NS}}}type", "bpmn:tFormalExpression")
                    ce.text = cond

                # nacktes <condition>...</condition>?
                for child in list(sf):
                    local = child.tag.rsplit("}", 1)[-1] if "}" in child.tag else child.tag
                    if local in ("condition", "conditionExpression") and child.tag != f"{{{BPMN_NS}}}conditionExpression":
                        raw = (child.text or "").strip()
                        if raw and not raw.startswith("${}"):
                            if not raw.startswith("${"):
                                raw = "${" + raw + "}"
                        sf.remove(child)
                        ce = ET.SubElement(sf, f"{{{BPMN_NS}}}conditionExpression")
                        ce.set(f"{{{XSI_NS}}}type", "bpmn:tFormalExpression")
                        ce.text = raw

            return ET.tostring(root, encoding="unicode")
        except Exception as e:
            logger.warning(f"BPMN sanitize failed, using original XML: {e}")
            return xml_string


    from typing import Optional, Tuple  # falls oben noch nicht importiert

    #Neu kann entfernt werden
    def _normalize_events_and_flows(self, xml_string: str) -> str:
        """
        Repariert typische Event/Flow-Fehler:
        - Entfernt alle SequenceFlows, die von EndEvents ausgehen oder in StartEvents enden.
        - Entfernt Event→Event-Ketten gleicher Art & gleichem Namen (z. B. End 'Cancelled' -> End 'Cancelled').
        - Entfernt eingehende Flows an StartEvents und ausgehende Flows an EndEvents (falls vorhanden).
        """
        try:
            ET.register_namespace("bpmn", BPMN_NS)
            ET.register_namespace("bpmndi", BPMNDI_NS)
            ET.register_namespace("di", DI_NS)
            ET.register_namespace("dc", DC_NS)

            root = ET.fromstring(xml_string)
            ns = {"bpmn": BPMN_NS}

            process = root.find(".//bpmn:process", ns)
            if process is None:
                return xml_string

            # Hilfsfunktionen
            def _local(el):
                return el.tag.rsplit("}", 1)[-1] if "}" in el.tag else el.tag

            def _etype(el):
                t = _local(el)
                if t == "startEvent":
                    return "start"
                if t == "endEvent":
                    return "end"
                if t == "boundaryEvent":
                    return "boundary"
                if t in ("intermediateCatchEvent", "intermediateThrowEvent"):
                    return "intermediate"
                return "other"

            # Indexe
            by_id = {}
            flows = []
            for el in list(process):
                eid = el.get("id")
                if not eid:
                    continue
                if _local(el) == "sequenceFlow":
                    flows.append(el)
                else:
                    by_id[eid] = el

            # 1) harte Regeln: EndEvent darf keine OUTs haben, StartEvent keine INs
            # -> wir entfernen entsprechende SequenceFlows
            removed_flows = set()

            for sf in flows:
                sid = sf.get("sourceRef")
                tid = sf.get("targetRef")
                src = by_id.get(sid)
                tgt = by_id.get(tid)
                if src is not None and _etype(src) == "end":
                    # End-Events dürfen keine ausgehenden Flows haben
                    process.remove(sf)
                    removed_flows.add(sf)
                elif tgt is not None and _etype(tgt) == "start":
                    # Start-Events dürfen keine eingehenden Flows haben
                    process.remove(sf)
                    removed_flows.add(sf)

            # Index aktualisieren nach Entfernung
            if removed_flows:
                flows = [f for f in flows if f not in removed_flows]

            # 2) Event→Event-Ketten gleicher Art & gleichem Namen auflösen
            #    Beispiel: End 'Cancelled' -> End 'Cancelled'  => entferne den Flow und das 2. (redundante) Event, falls sonst unverbunden
            removed_any = False
            for sf in list(flows):
                sid = sf.get("sourceRef")
                tid = sf.get("targetRef")
                src = by_id.get(sid)
                tgt = by_id.get(tid)
                if src is None or tgt is None:
                    continue
                es, et = _etype(src), _etype(tgt)
                if es in ("start", "end", "intermediate", "boundary") and et in ("start", "end", "intermediate", "boundary"):
                    # gleicher Name (case-insensitive, trim) und gleiche Event-Klasse?
                    name_s = (src.get("name") or "").strip().lower()
                    name_t = (tgt.get("name") or "").strip().lower()
                    if name_s and name_s == name_t and es == et:
                        # Flow entfernen
                        process.remove(sf)
                        removed_any = True
                        # Ziel-Event ggf. entfernen, wenn danach isoliert ist (keine Flows)
                        # In/Out pro ID ermitteln
                        tid_in = [f for f in process.findall(".//bpmn:sequenceFlow", ns) if f.get("targetRef") == tid]
                        tid_out = [f for f in process.findall(".//bpmn:sequenceFlow", ns) if f.get("sourceRef") == tid]
                        if len(tid_in) == 0 and len(tid_out) == 0:
                            process.remove(tgt)
                            by_id.pop(tid, None)

            # Optional: Boundary-Events säubern (keine eingehenden Flows, outgoing erlaubt)
            removed_flows2 = set()
            for sf in process.findall(".//bpmn:sequenceFlow", ns):
                sid = sf.get("sourceRef"); tid = sf.get("targetRef")
                src = by_id.get(sid); tgt = by_id.get(tid)
                if tgt is not None and _etype(tgt) == "boundary":
                    # boundaryEvent darf keine IN-Flows haben
                    process.remove(sf); removed_flows2.add(sf)
            if removed_flows2:
                pass  # nur zur Vollständigkeit

            return ET.tostring(root, encoding="unicode")
        except Exception as e:
            logger.warning(f"Normalize events/flows failed, using original XML: {e}")
            return xml_string

    def _ensure_bpmndi(self, xml_string: str) -> str:
        """
        Erzeugt minimales, konsistentes BPMN-DI (Diagram/Plane/Shapes/Edges), falls keines vorhanden ist.
        """
        try:
            ET.register_namespace("bpmn", BPMN_NS)
            ET.register_namespace("bpmndi", BPMNDI_NS)
            ET.register_namespace("di", DI_NS)
            ET.register_namespace("dc", DC_NS)

            root = ET.fromstring(xml_string)
            ns = {"bpmn": BPMN_NS, "bpmndi": BPMNDI_NS, "di": DI_NS, "dc": DC_NS}

            process = root.find(".//bpmn:process", ns)
            if process is None:
                return xml_string

            diagram = root.find(".//bpmndi:BPMNDiagram", ns)
            if diagram is None:
                diagram = ET.SubElement(root, f"{{{BPMNDI_NS}}}BPMNDiagram")
            plane = diagram.find("bpmndi:BPMNPlane", ns)
            if plane is None:
                plane = ET.SubElement(diagram, f"{{{BPMNDI_NS}}}BPMNPlane")
                plane.set("bpmnElement", process.get("id", "Process_1"))

            existing_shape_for = {sh.get("bpmnElement") for sh in plane.findall("bpmndi:BPMNShape", ns)}
            existing_edge_for  = {ed.get("bpmnElement") for ed in plane.findall("bpmndi:BPMNEdge", ns)}

            elements, flows = [], []
            for el in list(process):
                tag = el.tag.split('}', 1)[-1]
                if tag == "sequenceFlow":
                    flows.append(el)
                else:
                    elements.append(el)

            x, y = 120, 120
            dx, dy = 220, 140
            per_row = 6
            count = 0

            # Shapes
            for el in elements:
                eid = el.get("id")
                if not eid or eid in existing_shape_for:
                    continue
                shp = ET.SubElement(plane, f"{{{BPMNDI_NS}}}BPMNShape")
                shp.set("bpmnElement", eid)
                b = ET.SubElement(shp, f"{{{DC_NS}}}Bounds")
                tag = el.tag.split('}', 1)[-1]
                if tag.endswith("Event"):
                    w, h = 36, 36
                elif tag.endswith("Gateway"):
                    w, h = 50, 50
                else:
                    w, h = 120, 80
                b.set("x", str(x)); b.set("y", str(y))
                b.set("width", str(w)); b.set("height", str(h))
                count += 1
                if count % per_row == 0:
                    x = 120; y += dy
                else:
                    x += dx

            def _center(eid: str) -> Optional[Tuple[float, float]]:
                shp = plane.find(f"bpmndi:BPMNShape[@bpmnElement='{eid}']", ns)
                if shp is None:
                    return None
                b = shp.find("dc:Bounds", ns)
                if b is None:
                    return None
                bx, by = float(b.get("x", "0")), float(b.get("y", "0"))
                bw, bh = float(b.get("width", "100")), float(b.get("height", "80"))
                return (bx + bw/2.0, by + bh/2.0)

            # Edges
            for fl in flows:
                fid = fl.get("id")
                if not fid or fid in existing_edge_for:
                    continue
                s, t = fl.get("sourceRef"), fl.get("targetRef")
                p1, p2 = _center(s), _center(t)
                if not p1 or not p2:
                    continue
                ed = ET.SubElement(plane, f"{{{BPMNDI_NS}}}BPMNEdge")
                ed.set("bpmnElement", fid)
                wp1 = ET.SubElement(ed, f"{{{DI_NS}}}waypoint"); wp1.set("x", str(p1[0])); wp1.set("y", str(p1[1]))
                wp2 = ET.SubElement(ed, f"{{{DI_NS}}}waypoint"); wp2.set("x", str(p2[0])); wp2.set("y", str(p2[1]))

            return ET.tostring(root, encoding="unicode")
        except Exception as e:
            logger.warning(f"Ensure BPMN-DI failed, using original XML: {e}")
            return xml_string


    def _save_results(
        self, sid: str, bpmn_xml: str, plan: List[TransformationStep]
    ) -> Dict[str, Path]:
        """Save transformation results with versioning and timestamp"""
        gen_dir = self.sdl.get_session_dir(sid) / "generation"
        gen_dir.mkdir(parents=True, exist_ok=True)

        # Get iteration/version from state
        state_path = self.sdl.get_session_dir(sid) / "control" / "state.json"
        version = 1
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            version = int(state.get("generation_iteration", 0)) + 1

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

        # Save BPMN with version suffix
        output_path = gen_dir / f"tobe_bpmn_{sid}_v{version}_{ts}.bpmn"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(bpmn_xml)

        # Save transformation metadata with version suffix
        meta_path = gen_dir / f"tobe_meta_{sid}_v{version}_{ts}.json"
        meta = {
            "sid": sid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "transformation_plan": [
                {"action": step.action, "params": step.params, "order": step.order}
                for step in plan
            ],
            "agent": "HybridModelingAgent",
            "version": str(version),
        }

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        # Record in MongoDB
        self.sdl.record_artefact(
            sid=sid,
            phase="generation",
            artefact_type="tobe_bpmn",
            path=output_path,
            summary={"transformations": len(plan), "hybrid_approach": True},
        )

        self.sdl.record_artefact(
            sid=sid,
            phase="generation",
            artefact_type="tobe_meta",
            path=meta_path,
            summary={"transformations": len(plan), "hybrid_approach": True},
        )

        return {"tobe_bpmn_path": output_path, "tobe_meta_path": meta_path}
