from pathlib import Path
import json
import time
import uuid

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
import logging
logger = logging.getLogger(__name__)
# old SDL
# from core.shared_data_layer import SharedDataLayer
# new MongoDB-based SDL
from core.mongo_sdl import MongoSDL
from agents.Perception.process_mining_component import ProcessMiningComponent, DiscoveryConfig, DiscoveryMethod
from agents.Perception.interaction_agent import run_interaction
from agents.Perception.ingestion import run_integration_direct
from control.orchestrator import Orchestrator
from agents.interpretation.ist_process_interpreter_agent import ISTProcessInterpreterAgent
from agents.interpretation.process_modeling_software import BPMNRepository
from quality.interpretation_quality_check import InterpretationQualityAgent
from control.gates import write_gate_event
from agents.generation.infra import run_generation
from agents.evaluation.engine import EvaluationEngine



# Helpers

def _find_clean_xes_path(sid, sdl):
    """
    Search the cleaned XES file in SDL. Supports both variants:
    - perception/clean_xes_<sid>.xes
    - perception/clean_xes.xes
    """
    candidates = [
        sdl.sdl_root / sid / "perception" / f"clean_xes_{sid}.xes",
        sdl.sdl_root / sid / "perception" / "clean_xes.xes",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

def _emit_perception_done(orch, sid, sdl, ingestion_result=None, pm_result=None):
    """
    Emit perception.done with BOTH ingestion AND process mining results
    """
    if not ingestion_result:
        raise ValueError("ingestion_result missing")

    qc_pass = bool(ingestion_result.get("qc_pass"))
    pm_success = pm_result and pm_result.get("status") == "success"
    
    # Overall success if EITHER passes
    overall_success = qc_pass or pm_success
    event_type = "perception.done" if overall_success else "perception.fail"

    artefacts = {
        "integrated_events": ingestion_result.get("integrated_path"),
        "dq_report": ingestion_result.get("dq_report_path"),
    }
    
    if ingestion_result.get("clean_xes_path"):
        artefacts["clean_xes"] = ingestion_result["clean_xes_path"]
    
    # ADD process mining artefacts
    if pm_success:
        artefacts["discovered_bpmn"] = pm_result.get("ist_bpmn_path")
        artefacts["performance_data"] = pm_result.get("performance_data_path")

    event = {
        "type": event_type,
        "session_id": sid,
        "event_id": str(uuid.uuid4()),
        "payload": {
            "dq_status": "GREEN" if qc_pass else ("YELLOW" if pm_success else "RED"),
            "process_mining_status": "SUCCESS" if pm_success else "FAILED",
            "stats": ingestion_result.get("stats", {}),
        },
        "artefacts": artefacts,
    }

    gate_path, _ = write_gate_event(
        sdl=sdl, sid=sid, gate="perception",
        payload=event["payload"],
        artefacts=artefacts,
        event_type=event_type
    )
    print(f"[Perception Gate] {event_type} written:", gate_path)
    orch.handle_event(event)


def _emit_interpretation_done(orch, sid, res, qa_result=None, repo_result=None):
    """
    Create an interpretation.done event via Gate and forward to Orchestrator.
    """
    artefacts = {
        "ist_bpmn": res.get("ist_bpmn_path"),
        "model_meta": res.get("model_meta_path"),
    }
    if qa_result:
        artefacts["as_is_quality"] = qa_result.get("quality_path")
    if repo_result:
        artefacts["repo_model_id"] = repo_result.get("model_id")
        artefacts["repo_version"] = repo_result.get("version")

    payload = {"note": "interpreter finished"}
    if qa_result:
        payload["quality"] = qa_result.get("as_is_quality")

    # 1) Gate schreiben
    gate_path, event = write_gate_event(
        sdl=sdl,
        sid=sid,
        gate="interpretation",
        payload=payload,
        artefacts=artefacts
    )
    print("Interpretation Gate written:", gate_path)

    # 2) Orchestrator informieren
    orch.handle_event(event)

def _emit_evaluation_done(orch, sid, sdl, eval_result=None):
    """
    Create an evaluation.done event via Gate und forward to Orchestrator.
    """
    payload = {
        "score": (eval_result or {}).get("score", 0.0),
        "summary": (eval_result or {}).get("summary", {}),
    }
    artefacts = {
        "tobe_bpmn": (eval_result or {}).get("tobe_bpmn"),
        "eval_report": (eval_result or {}).get("report_path"),
    }

    gate_path, event = write_gate_event(
        sdl=sdl,
        sid=sid,
        gate="evaluation",
        payload=payload,
        artefacts=artefacts,
        event_type="evaluation.done"
    )
    print("Evaluation Gate written:", gate_path)
    orch.handle_event(event)


# Main

if __name__ == "__main__":
    # 1) User input
    prompt = input("Kontext: ").strip()

    # 2) Initialize Shared Data Layer (now MongoDB-backed)
    sdl = MongoSDL()

    # 3) Run Interaction Agent
    sid, meta, paths = run_interaction(
        user_prompt=prompt,
        uploads=[Path(r"BPI_xes/BPI Challenge 2017.xes.gz")],  # <— pfad zu r XES-Datei
        sdl=sdl,
    )
    # 4) Run Process Mining FIRST (raw data)
    print("\n--- Process Mining ---")
    from agents.Perception.process_mining_component import ProcessMiningComponent, DiscoveryConfig

    pm_config = DiscoveryConfig(
        method=DiscoveryMethod.INDUCTIVE,
        noise_threshold=0.2,
        noise_grid=(0.2,),  # <-- ONLY ONE VALUE it can also be noise_grid=(0.10, 0.20, 0.30) for multiple runs
        compute_conformance=True,
        try_both_methods=False  # <-- Don't try DFG too
    )
    pm_component = ProcessMiningComponent(sdl, pm_config)
    pm_result = pm_component.run(sid)

    if pm_result.get("status") == "success":
        print(f"[PM] Success: {pm_result['ist_bpmn_path']}")
    else:
        print(f"[PM] Failed: {pm_result.get('error')}")
    print("\n--- Interaction Agent Output ---")
    print("Session ID:", sid)
    print("Meta:", meta)
    print("Upload Paths:", paths)

    # 4) Run Ingestion + Data Quality
    result = run_integration_direct(sid, meta, paths, sdl=sdl)
    print("\n--- Ingestion & DQ Output ---")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 5) Initialize Orchestrator
    orch = Orchestrator()

    # 6) Emit perception.done event to Orchestrator (nur Gate weiterreichen!)
    _emit_perception_done(orch, sid, sdl, 
                     ingestion_result=result,
                     pm_result=pm_result)

    # 6.5) Initialize BPMN repository (versioned model storage)
    repo = BPMNRepository("data/models")

    # 7) Orchestrator Command Loop
    print("\n--- Orchestrator Command Loop ---")
    control_dir = sdl.sdl_root / sid / "control"
    processed = set()
    idle_rounds = 0
    MAX_IDLE_ROUNDS = 40 

    while idle_rounds < MAX_IDLE_ROUNDS:
        found = False
        for cmd_file in control_dir.glob("cmd_*_start.json"):
            key = (cmd_file, cmd_file.stat().st_mtime_ns)
            if key in processed:
                continue

            found = True

            cmd = json.loads(cmd_file.read_text(encoding="utf-8"))
            target = cmd.get("target")
            action = cmd.get("action")
            print(f"[Command] target={target} action={action}")

            if target == "interpretation" and action == "start":
                agent = ISTProcessInterpreterAgent(sdl)
                res = agent.run(sid)
                print("As-Is BPMN:", res["ist_bpmn_path"])
                print("Meta:", res["model_meta_path"])

                qa = InterpretationQualityAgent(sdl)
                qa_result = qa.run(
                    sid=sid,
                    ist_bpmn_path=res["ist_bpmn_path"],
                    clean_xes_path=result.get("clean_xes_path")  # prüf den exakten Key
                )
                print("As-Is Quality:", qa_result["as_is_quality"])

                bpmn_file = Path(res["ist_bpmn_path"])
                bpmn_xml = bpmn_file.read_text(encoding="utf-8")
                repo_result = repo.create_model(
                    session_id=sid,
                    bpmn_xml=bpmn_xml,
                    source="as_is",
                    origin_agent="ISTProcessInterpreterAgent",
                    label=f"as_is_{sid}",
                    parent_model_id=None,
                    tags=[]
                )
                print("Repository create:", repo_result)

                _emit_interpretation_done(
                    orch, sid, res,
                    qa_result=qa_result,
                    repo_result=repo_result
                )
                processed.add((cmd_file, cmd_file.stat().st_mtime_ns))


            elif target == "generation" and action == "start":
                gen_res = run_generation(
                    sid,
                    sdl,
                    orchestrator=orch,
                    strategies=["time"],
                    llm_model="gpt-5-mini",
                    add_auto_di=True,
                )
                print("Generation result:", json.dumps(gen_res, ensure_ascii=False, indent=2))
                processed.add((cmd_file, cmd_file.stat().st_mtime_ns))


            elif target == "evaluation" and action == "start":
                print("[evaluation] start")
                engine = EvaluationEngine(sdl)
                eval_res = engine.run(sid)  # zieht Pfade aus SDL/Mongo
                # Read and display summaries from FILES
                eval_dir = sdl.sdl_root / sid / "evaluation"
                #Neu
                # 1. Read comparison report
                comp_files = sorted(eval_dir.glob("comparison_report_*.json"), 
                                key=lambda p: p.stat().st_mtime, reverse=True)
                if comp_files:
                    print(f"\n[Found Comparison Report]: {comp_files[0].name}")
                    with open(comp_files[0], 'r') as f:
                        comp = json.load(f)
                        print(f"  Score: {comp.get('score')}/100")
                        print(f"  Recommendation: {comp.get('recommendation', {}).get('type')}")

                # 2. Read evaluation summary
                summary_files = sorted(eval_dir.glob("evaluation_summary_*.json"),
                                    key=lambda p: p.stat().st_mtime, reverse=True)  
                if summary_files:
                    print(f"\n[Found Evaluation Summary]: {summary_files[0].name}")
                    with open(summary_files[0], 'r') as f:
                        summ = json.load(f)
                        print(f"  Ready for implementation: {summ.get('overall', {}).get('ready_for_implementation')}")

                # 3. Read executive summary if exists
                exec_files = list(eval_dir.glob("executive_summary_*.md"))
                if exec_files:
                    print(f"\n[Found Executive Summary]: {exec_files[0].name}")
                    # Content is in the file, just note it exists
                artefacts = {
                    "baseline_metrics": eval_res.get("baseline_path"),
                    "sim_tobe_metrics": eval_res.get("sim_path"),
                    "comparison_report": eval_res.get("comparison_report_path"),
                    "evaluation_summary": eval_res.get("summary_path"),
                    "tobe_bpmn": eval_res.get("tobe_bpmn"),
                }

                prio = eval_res.get("prioritization") or {}
                for k in ("priority_backlog_path", "agent_evaluation_report_path", "executive_summary_path"):
                    if prio.get(k):
                        artefacts[k] = prio[k]

                payload = {
                    "deltas": eval_res.get("deltas"),
                    "score": eval_res.get("score"),
                    "recommendation": eval_res.get("recommendation"),
                    "on_time_p90": eval_res.get("on_time_p90"),
                }

                gate_path, event = write_gate_event(
                    sdl=sdl,
                    sid=sid,
                    gate="evaluation",
                    payload=payload,
                    artefacts=artefacts,
                    event_type="evaluation.done",
                )
                print("[evaluation] Gate written:", gate_path)
                orch.handle_event(event)
                # Update state with iteration count
                state_path = control_dir / "state.json"
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                else:
                    state = {}

                state["generation_iteration"] = state.get("generation_iteration", 0) + 1
                state["evaluation_count"] = state.get("evaluation_count", 0) + 1
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                logger.info(f"Updated state: generation_iteration={state['generation_iteration']}")
                # <-- erst NACH erfolgreicher Verarbeitung markieren
                #processed.add(cmd_file)
                processed.add((cmd_file, cmd_file.stat().st_mtime_ns))


            elif target == "exploration" and action == "start":
                from agents.feedback.exploration_infra import ExplorationInfra
                from agents.feedback.feedback_collector import FeedbackCollector

                infra = ExplorationInfra(sdl)
                res = infra.run(sid)  # erzeugt insights/visuals/explainability

                fc = FeedbackCollector(sdl)
                print("\n[feedback] Exploration abgeschlossen. Entscheidung erforderlich...\n")
                decision = fc.interactive_cli(sid)  # kein orchestrator-arg

                # Wenn der Collector ein Event zurückgibt: weiterleiten
                if isinstance(decision, dict) and "event" in decision:
                    orch.handle_event(decision["event"])
                # (Wenn der Collector intern schon handle_event aufruft, ist das hier einfach ein No-Op.)

                processed.add((cmd_file, cmd_file.stat().st_mtime_ns))




            elif target == "feedback" and action == "start":
                # Feedback Collector: Paket für UI bereitstellen
                from agents.feedback.feedback_collector import FeedbackCollector
                fc = FeedbackCollector(sdl)
                payload = fc.prepare(sid)  # enthält Pfade zu Explanation/Visuals + Issues
                print("[feedback] payload ready:", payload.get("ready", False))
                # UI sammelt die Entscheidung ein und ruft fc.submit(...) – hier nur bereitstellen
                processed.add((cmd_file, cmd_file.stat().st_mtime_ns))

