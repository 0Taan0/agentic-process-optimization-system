# streamlit_app.py
"""
Enhanced Process AI Streamlit Application with Camunda BPMN Viewer
"""
import json
import time
import uuid
from pathlib import Path
import sys
import streamlit as st
from datetime import datetime
import base64
import streamlit.components.v1 as components


from agents.generation.improved_modeling_agent import ImprovedModelingAgent
from pathlib import Path
import json, time
from core.mongo_sdl import MongoSDL
from agents.Perception.interaction_agent import run_interaction
from agents.Perception.ingestion import run_integration_direct
from control.orchestrator import Orchestrator
from control.gates import write_gate_event
from agents.interpretation.ist_process_interpreter_agent import ISTProcessInterpreterAgent
from agents.interpretation.process_modeling_software import BPMNRepository
from quality.interpretation_quality_check import InterpretationQualityAgent
from agents.generation.infra import run_generation
from agents.evaluation.engine import EvaluationEngine
from agents.feedback.exploration_infra import ExplorationInfra
from agents.feedback.feedback_collector import FeedbackCollector

# Add project root to path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ----------------- Configuration -----------------
st.set_page_config(
    page_title="Process AI Complete Workflow", 
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'About': "Process AI - Automated Business Process Optimization"
    }
)

# ----------------- Custom CSS -----------------
st.markdown("""
<style>
    /* Main app styling */
    .stApp {
        background-color: #f8f9fa;
    }
    
    /* Improve metrics display */
    [data-testid="metric-container"] {
        background-color: white;
        border: 1px solid #e0e0e0;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    /* Better button styling */
    .stButton > button {
        background-color: #007bff;
        color: white;
        border-radius: 5px;
        border: none;
        padding: 0.5rem 1rem;
        font-weight: 500;
        transition: all 0.3s ease;
    }
    
    .stButton > button:hover {
        background-color: #0056b3;
        transform: translateY(-1px);
        box-shadow: 0 4px 8px rgba(0,0,0,0.2);
    }
    
    /* Phase status cards */
    .phase-card {
        background: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        margin: 10px 0;
        border-left: 4px solid #007bff;
    }
    
    .phase-complete {
        border-left-color: #28a745;
    }
    
    .phase-running {
        border-left-color: #ffc107;
    }
    
    .phase-pending {
        border-left-color: #6c757d;
    }
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
        background-color: white;
        padding: 10px;
        border-radius: 10px;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        padding-left: 20px;
        padding-right: 20px;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- BPMN Viewer Component -----------------
def render_bpmn_viewer(bpmn_xml: str, height: int = 600):
    """Render BPMN using bpmn-js (Camunda's viewer)"""
    
    # Escape the XML for JavaScript
    escaped_xml = bpmn_xml.replace('`', '\\`').replace('${', '\\${')
    
    # HTML template with bpmn-js
    html_template = f"""
    <div id="canvas" style="height: {height}px; width: 100%; border: 1px solid #ccc; background: white;"></div>
    
    <link rel="stylesheet" href="https://unpkg.com/bpmn-js@11.5.0/dist/assets/bpmn-js.css">
    <link rel="stylesheet" href="https://unpkg.com/bpmn-js@11.5.0/dist/assets/diagram-js.css">
    <link rel="stylesheet" href="https://unpkg.com/bpmn-js@11.5.0/dist/assets/bpmn-font/css/bpmn.css">
    
    <script src="https://unpkg.com/bpmn-js@11.5.0/dist/bpmn-viewer.development.js"></script>
    
    <script>
        // BPMN XML
        var bpmnXML = `{escaped_xml}`;
        
        // Create viewer
        var viewer = new BpmnJS({{
            container: '#canvas'
        }});
        
        // Import and render
        viewer.importXML(bpmnXML).then(function(result) {{
            const warnings = result.warnings;
            
            if (warnings.length) {{
                console.log('Warnings:', warnings);
            }}
            
            // Zoom to fit
            var canvas = viewer.get('canvas');
            canvas.zoom('fit-viewport');
            
            // Center
            canvas.zoom('fit-viewport', {{ x: 150, y: 150 }});
            
        }}).catch(function(err) {{
            console.error('Error rendering BPMN:', err);
        }});
        
        // Add pan and zoom controls
        document.addEventListener('wheel', function(e) {{
            if (e.ctrlKey) {{
                e.preventDefault();
                var canvas = viewer.get('canvas');
                var zoom = canvas.zoom();
                canvas.zoom(zoom * (e.deltaY > 0 ? 0.9 : 1.1));
            }}
        }});
    </script>
    
    <div style="margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 5px;">
        <small>💡 Use Ctrl+Scroll to zoom, drag to pan. Powered by Camunda bpmn-js.</small>
    </div>
    """
    
    components.html(html_template, height=height + 50)

# ----------------- Helper Functions -----------------
def create_download_button(file_path: Path, label: str = "Download"):
    """Create a download button for a file"""
    with open(file_path, "rb") as file:
        contents = file.read()
        b64 = base64.b64encode(contents).decode()
        href = f'<a href="data:application/octet-stream;base64,{b64}" download="{file_path.name}" class="download-btn">{label}</a>'
        st.markdown(href, unsafe_allow_html=True)

def display_phase_card(phase: str, status: str, details: dict = None):
    """Display a nice phase status card"""
    status_class = {
        "✅ Complete": "phase-complete",
        "🔄 Running": "phase-running", 
        "⏸️ Pending": "phase-pending"
    }.get(status, "phase-pending")
    
    icon = {
        "perception": "",
        "interpretation": "",
        "generation": "",
        "evaluation": "",
        "exploration": "",
        "feedback": ""
    }.get(phase, "")
    
    html = f"""
    <div class="phase-card {status_class}">
        <h4>{icon} {phase.title()}</h4>
        <p style="margin: 5px 0; color: #666;">{status}</p>
    """
    
    if details:
        for key, value in details.items():
            html += f"<small><b>{key}:</b> {value}</small><br>"
    
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

# Your existing helper functions...
def get_session_state(sid):
    """Get current state of a session"""
    if not sid:
        return None
    
    state_path = st.session_state.sdl.sdl_root / sid / "control" / "state.json"
    if state_path.exists():
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except:
            return None
    return None

def _write_gate(sdl, sid, gate, payload=None, artefacts=None, event_type=None):
    # Minimaler Gate-Writer
    ctrl = Path(sdl.sdl_root) / sid / "control"
    ctrl.mkdir(parents=True, exist_ok=True)
    evt = {
        "sid": sid,
        "gate": gate,
        "event": event_type or f"{gate}.done",
        "payload": payload or {},
        "artefacts": artefacts or {},
    }
    out = ctrl / f"{gate}.done.json"
    out.write_text(json.dumps(evt, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)

def get_phase_status(sid, phase):
    control_dir = st.session_state.sdl.sdl_root / sid / "control"
    # Done?
    if any(control_dir.glob(f"{phase}.done*.json")):
        return "✅ Complete"
    # Läuft?
    if (control_dir / f"cmd_{phase}_start.json").exists():
        return "🔄 Running"
    # Sonst
    return "⏸️ Pending"


# ----------------- Session State -----------------
if 'orchestrator' not in st.session_state:
    st.session_state.orchestrator = Orchestrator()

if 'sdl' not in st.session_state:
    st.session_state.sdl = MongoSDL()

if 'current_sid' not in st.session_state:
    st.session_state.current_sid = None

if 'workflow_state' not in st.session_state:
    st.session_state.workflow_state = {}

if 'bpmn_repo' not in st.session_state:
    st.session_state.bpmn_repo = BPMNRepository("data/models")

# ----------------- Main UI -----------------
# Header with logo/branding
col1, col2 = st.columns([1, 4])
with col1:
    st.markdown("# ")
with col2:
    st.title("Process AI - Complete Workflow")
    st.markdown("*Automated Business Process Optimization using AI*")

# Sidebar for session management
with st.sidebar:
    st.markdown("##  Session Management")
    
    # List existing sessions
    sessions = []
    if st.session_state.sdl.sdl_root.exists():
        sessions = sorted([p.name for p in st.session_state.sdl.sdl_root.iterdir() 
                          if p.is_dir() and p.name.startswith("sid-")], 
                         reverse=True)
    
    if sessions:
        selected_sid = st.selectbox("Select session:", 
                                    [" New Session"] + sessions,
                                    index=0 if not st.session_state.current_sid else 
                                          sessions.index(st.session_state.current_sid) + 1 
                                          if st.session_state.current_sid in sessions else 0)
        
        if selected_sid != " New Session":
            st.session_state.current_sid = selected_sid
    
    if st.session_state.current_sid:
        st.success(f" Current: {st.session_state.current_sid}")
        
        # Show session details in expandable
        with st.expander("Session Details"):
            state = get_session_state(st.session_state.current_sid)
            if state:
                st.json({
                    "current_phase": state.get("current_phase"),
                    "last_event": state.get("last_event", {}).get("type"),
                    "created": state.get("created_at", "Unknown")
                })
    
    st.markdown("---")
    
    # Quick actions
    st.markdown("###  Quick Actions")
    if st.button(" Refresh Status", use_container_width=True):
        st.rerun()
    
    if st.session_state.current_sid:
        session_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid
        if st.button(" Open Session Folder", use_container_width=True):
            st.info(f"Path: {session_dir}")

# Main content area
if not st.session_state.current_sid or st.sidebar.button("🆕 Start New Session", use_container_width=True):
    # New session UI
    st.markdown("##  Start New Process Analysis")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        with st.form("new_session_form", clear_on_submit=True):
            st.markdown("###  Process Context")
            context = st.text_area(
                "Describe your process problem:", 
                placeholder="Example: Our credit application process is too slow. Customers complain about long waiting times and manual steps. We need to reduce cycle time while maintaining compliance...",
                height=150
            )
            
            st.markdown("###  Process Data")
            uploaded_file = st.file_uploader(
                "Upload Process Event Log:", 
                type=['xes', 'gz'],
                help="Upload your process log in XES or XES.GZ format"
            )
            
            col_a, col_b = st.columns(2)
            with col_a:
                submit = st.form_submit_button(" Start Analysis", use_container_width=True, type="primary")
            with col_b:
                cancel = st.form_submit_button(" Cancel", use_container_width=True)
            
            if submit and context and uploaded_file:
                # Process new session (your existing code)
                temp_path = Path(f"temp_{uploaded_file.name}")
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                try:
                    with st.spinner(" Creating session and analyzing data quality..."):
                        # Your existing session creation code...
                        sid, meta, paths = run_interaction(
                            user_prompt=context,
                            uploads=[temp_path],
                            sdl=st.session_state.sdl,
                        )
                        st.session_state.current_sid = sid
                    
                    st.success(f"✅ Session created: {sid}")
                    
                    # Run ingestion & quality check
                    with st.spinner("🔍 Analyzing data quality..."):
                        result = run_integration_direct(sid, meta, paths, sdl=st.session_state.sdl)
                    
                    # Your existing event handling code...
                    qc_pass = bool(result.get("qc_pass"))
                    if qc_pass:
                        st.success("✅ Data quality check passed!")
                        st.balloons()
                    else:
                        st.error("❌ Data quality check failed!")
                    
                    # Create perception event
                    artefacts = {
                        "integrated_events": result.get("integrated_path"),
                        "dq_report": result.get("dq_report_path"),
                    }
                    if result.get("clean_xes_path"):
                        artefacts["clean_xes"] = result["clean_xes_path"]
                    
                    gate_path, event = write_gate_event(
                        sdl=st.session_state.sdl,
                        sid=sid,
                        gate="perception",
                        payload={
                            "dq_status": "GREEN" if qc_pass else "RED",
                            "stats": result.get("stats", {}),
                        },
                        artefacts=artefacts,
                        event_type="perception.done" if qc_pass else "perception.fail"
                    )
                    
                    st.session_state.orchestrator.handle_event(event)
                    
                    if qc_pass:
                        time.sleep(1)
                        st.rerun()
                
                finally:
                    if temp_path.exists():
                        temp_path.unlink()
    
    with col2:
        # Help section
        st.markdown("### Tips for Success")
        st.info("""
        **Good Context Description:**
        - Specific process problems
        - Current pain points
        - Desired improvements
        - Any constraints
        
        **Data Requirements:**
        - XES format event log
        - Minimum 100 cases
        - Complete timestamps
        """)

else:
    # Workflow view for existing session
    st.markdown(f"##  Workflow Progress")
    st.markdown(f"**Session:** `{st.session_state.current_sid}`")
    
    # Progress overview
    with st.container():
        phases = ["perception", "interpretation", "generation", "evaluation", "exploration", "feedback"]
        cols = st.columns(6)
        
        for i, phase in enumerate(phases):
            with cols[i]:
                status = get_phase_status(st.session_state.current_sid, phase)
                display_phase_card(phase, status)
    
    # Detailed tabs
    st.markdown("---")
    tabs = st.tabs([
        " Overview", 
        " Interpretation", 
        " Generation", 
        " Evaluation",
        " Exploration",
        " Decision"
    ])
    
    # Tab 0: Overview
    with tabs[0]:
        st.markdown("###  Process Optimization Journey")
        
        # Session info
        meta = st.session_state.sdl.read_session_meta(st.session_state.current_sid)
        if meta:
            with st.expander(" Session Context", expanded=True):
                st.markdown(f"**Problem Statement:** {meta.get('user_prompt', 'N/A')}")
                st.markdown(f"**Created:** {meta.get('created_at', 'Unknown')}")
        
        # Current BPMN models comparison
        col1, col2 = st.columns(2)
        
        # AS-IS BPMN
        with col1:
            st.markdown("#### 🔴 AS-IS Process")
            interp_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "interpretation"
            ist_bpmn = None
            if interp_dir.exists():
                bpmn_files = list(interp_dir.glob("*.bpmn"))
                if bpmn_files:
                    ist_bpmn = bpmn_files[0]
                    with st.expander("View AS-IS BPMN", expanded=False):
                        bpmn_xml = ist_bpmn.read_text(encoding="utf-8")
                        render_bpmn_viewer(bpmn_xml, height=400)
                    create_download_button(ist_bpmn, " Download AS-IS BPMN")
        
        # TO-BE BPMN
        with col2:
            st.markdown("#### 🟢 TO-BE Process")
            gen_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "generation"
            tobe_bpmn = None
            if gen_dir.exists():
                bpmn_files = list(gen_dir.glob("tobe_*.bpmn"))
                if bpmn_files:
                    tobe_bpmn = bpmn_files[0]
                    with st.expander("View TO-BE BPMN", expanded=False):
                        bpmn_xml = tobe_bpmn.read_text(encoding="utf-8")
                        render_bpmn_viewer(bpmn_xml, height=400)
                    create_download_button(tobe_bpmn, " Download TO-BE BPMN")
    
    # Tab 1: Interpretation
    with tabs[1]:
        st.markdown("### Process Interpretation")
        
        status = get_phase_status(st.session_state.current_sid, "interpretation")
        
        if status == "✅ Complete":
            st.success(" Interpretation completed successfully!")
            
            # Show BPMN
            interp_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "interpretation"
            if interp_dir.exists():
                bpmn_files = list(interp_dir.glob("*.bpmn"))
                if bpmn_files:
                    st.markdown("#### Generated AS-IS BPMN Model")
                    bpmn_xml = bpmn_files[0].read_text(encoding="utf-8")
                    render_bpmn_viewer(bpmn_xml, height=500)
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        create_download_button(bpmn_files[0], " Download BPMN")
                    
                    # Show quality metrics
                    quality_files = list(interp_dir.glob("*quality*.json"))
                    if quality_files:
                        with col2:
                            if st.button(" View Quality Report"):
                                quality_data = json.loads(quality_files[0].read_text())
                                st.json(quality_data)
        
        elif status == "🔄 Running":
            st.info(" Interpretation is currently running...")
            st.spinner("Processing...")
            time.sleep(0.5)
        
        else:
            control_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "control"
            if (control_dir / "cmd_interpretation_start.json").exists():
                if st.button("▶️ Run Interpretation", type="primary", use_container_width=True):
                    with st.spinner(" Discovering process model from event log..."):
                        # Your existing interpretation code...
                        cmd = json.loads((control_dir / "cmd_interpretation_start.json").read_text())
                        
                        agent = ISTProcessInterpreterAgent(st.session_state.sdl)
                        res = agent.run(st.session_state.current_sid)
                        
                        qa = InterpretationQualityAgent(st.session_state.sdl)
                        qa_result = qa.run(
                            sid=st.session_state.current_sid,
                            ist_bpmn_path=res["ist_bpmn_path"],
                            clean_xes_path=None
                        )
                        
                        # Save to repository
                        bpmn_file = Path(res["ist_bpmn_path"])
                        bpmn_xml = bpmn_file.read_text(encoding="utf-8")
                        repo_result = st.session_state.bpmn_repo.create_model(
                            session_id=st.session_state.current_sid,
                            bpmn_xml=bpmn_xml,
                            source="as_is",
                            origin_agent="ISTProcessInterpreterAgent",
                            label=f"as_is_{st.session_state.current_sid}",
                        )
                        
                        # Create done event
                        artefacts = {
                            "ist_bpmn": res.get("ist_bpmn_path"),
                            "model_meta": res.get("model_meta_path"),
                            "as_is_quality": qa_result.get("quality_path"),
                            "repo_model_id": repo_result.get("model_id"),
                        }
                        
                        gate_path, event = write_gate_event(
                            sdl=st.session_state.sdl,
                            sid=st.session_state.current_sid,
                            gate="interpretation",
                            payload={"quality": qa_result.get("as_is_quality")},
                            artefacts=artefacts,
                            event_type="interpretation.done",
                        )

                        
                        st.session_state.orchestrator.handle_event(event)
                        st.success("✅ Interpretation completed!")
                        
                        (control_dir / "cmd_interpretation_start.json").unlink()
                        st.rerun()
    
    # Tab 2: Generation (your existing code with UI improvements)
    with tabs[2]:
        st.markdown("###  Process Optimization Generation")
        
        status = get_phase_status(st.session_state.current_sid, "generation")
        
        if status == "✅ Complete":
            st.success("✅ Generation completed successfully!")
            
            # Show TO-BE BPMN
            gen_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "generation"
            if gen_dir.exists():
                bpmn_files = list(gen_dir.glob("tobe_*.bpmn"))
                if bpmn_files:
                    st.markdown("####  Generated TO-BE BPMN Model")
                    bpmn_xml = bpmn_files[0].read_text(encoding="utf-8")
                    render_bpmn_viewer(bpmn_xml, height=500)
                    
                    create_download_button(bpmn_files[0], " Download TO-BE BPMN")
                    
                    # Show improvements summary
                    summary_files = list(gen_dir.glob("*summary*.json"))
                    if summary_files:
                        summary = json.loads(summary_files[0].read_text())
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Total Suggestions", sum(summary.get("suggestion_counts", {}).values()))
                        with col2:
                            st.metric("Applied", summary.get("applied_count", 0))
                        with col3:
                            st.metric("Strategies Used", len(summary.get("suggestion_counts", {})))
        
        else:
            control_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "control"
            
            if (control_dir / "cmd_generation_start.json").exists():
                # Your existing auto-detection code...
                meta = st.session_state.sdl.read_session_meta(st.session_state.current_sid)
                user_prompt = meta.get("user_prompt", "").lower()
                
                # Auto-detect strategies
                strategies = []
                strategy_reasons = []
                
                # Time keywords
                if any(kw in user_prompt for kw in ["slow", "delay", "time", "duration", "cycle", "faster"]):
                    strategies.append("time")
                    strategy_reasons.append("⏱️ Time optimization")
                
                # Cost keywords
                if any(kw in user_prompt for kw in ["cost", "expensive", "budget", "money"]):
                    strategies.append("cost")
                    strategy_reasons.append(" Cost optimization")
                
                # Default
                if not strategies:
                    strategies = ["time", "quality"]
                
                st.info(f"🔍 Auto-detected focus: {', '.join(strategy_reasons)}")
                
                # Generation settings
                with st.form("generation_settings"):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        model = st.selectbox("🤖 AI Model:", ["gpt-5-mini", "gpt-5"])
                    
                    with col2:
                        max_suggestions = st.slider("Max Suggestions:", 5, 20, 10)
                    
                    submitted = st.form_submit_button("🚀 Generate TO-BE Process", type="primary", use_container_width=True)
                    
                    if submitted:
                        with st.spinner(f" Generating optimized process..."):
                            gen_res = run_generation(
                                st.session_state.current_sid,
                                st.session_state.sdl,
                                orchestrator=st.session_state.orchestrator,
                                strategies=strategies,
                                llm_model=model,
                                add_auto_di=True,
                            )
                            
                            if gen_res.get("status") == "success":
                                st.success("✅ Generation completed!")
                                (control_dir / "cmd_generation_start.json").unlink()
                                st.rerun()
                            else:
                                st.error(f"Generation failed: {gen_res.get('error')}")
    
    # Tab 3: Evaluation
    with tabs[3]:
        st.markdown("###  Process Evaluation & Comparison")
        
        status = get_phase_status(st.session_state.current_sid, "evaluation")
        
        if status == "✅ Complete":
            st.success("✅ Evaluation completed!")
            
            # Load and display results
            eval_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "evaluation"
            comparison_files = list(eval_dir.glob("comparison_report*.json"))
            
            if comparison_files:
                comparison = json.loads(comparison_files[0].read_text())
                
                # Key metrics
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    score = comparison.get("score", 0)
                    st.metric("Overall Score", f"{score:.0f}/100",
                             delta=f"{score-50:.0f}" if score != 50 else None)
                
                with col2:
                    time_imp = comparison.get("time_improvements", {}).get("cycle_mean_pct", 0)
                    st.metric("Time Reduction", f"{abs(time_imp):.1f}%",
                             delta=f"{time_imp:.1f}%" if time_imp != 0 else None)
                
                with col3:
                    st.metric("Risks", len(comparison.get("risks", [])),
                             delta_color="inverse")
                
                with col4:
                    st.metric("Benefits", len(comparison.get("benefits", [])))
                
                # Recommendation
                rec = comparison.get("recommendation", {})
                rec_type = rec.get("type", "")
                
                if rec_type == "proceed_to_decision":
                    st.success(f"✅ **Recommendation:** {rec_type}")
                elif rec_type == "proceed_with_caveats":
                    st.warning(f" **Recommendation:** {rec_type}")
                else:
                    st.error(f" **Recommendation:** {rec_type}")
                
                st.info(rec.get("rationale", ""))
                
                # Details in tabs
                detail_tabs = st.tabs([" Improvements", " Risks", " Benefits", " Full Report"])
                
                with detail_tabs[0]:
                    st.json(comparison.get("time_improvements", {}))
                
                with detail_tabs[1]:
                    for risk in comparison.get("risks", []):
                        st.warning(risk)
                
                with detail_tabs[2]:
                    for benefit in comparison.get("benefits", []):
                        st.success(benefit)
                
                with detail_tabs[3]:
                    st.json(comparison)
        
        else:
            control_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "control"
            
            if (control_dir / "cmd_evaluation_start.json").exists():
                if st.button("▶ Run Evaluation", type="primary", use_container_width=True):
                    with st.spinner(" Evaluating process improvements..."):
                        engine = EvaluationEngine(st.session_state.sdl)
                        eval_res = engine.run(st.session_state.current_sid)
                        
                        # Your existing event handling...
                        artefacts = {
                            "baseline_metrics": eval_res.get("baseline_path"),
                            "sim_tobe_metrics": eval_res.get("sim_path"),
                            "comparison_report": eval_res.get("comparison_report_path"),
                            "evaluation_summary": eval_res.get("summary_path"),
                            "tobe_bpmn": eval_res.get("tobe_bpmn"),
                        }
                        
                        payload = {
                            "deltas": eval_res.get("deltas"),
                            "score": eval_res.get("score"),
                            "recommendation": eval_res.get("recommendation"),
                            "on_time_p90": eval_res.get("on_time_p90"),
                        }
                        
                        gate_path, event = write_gate_event(
                            sdl=st.session_state.sdl,
                            sid=st.session_state.current_sid,
                            gate="evaluation",
                            payload=payload,
                            artefacts=artefacts,
                            event_type="evaluation.done",
                        )
                        
                        st.session_state.orchestrator.handle_event(event)
                        (control_dir / "cmd_evaluation_start.json").unlink()
                        st.rerun()
    
    # Tab 4: Exploration
    with tabs[4]:
        st.markdown("### Process Insights & Exploration")
        
        status = get_phase_status(st.session_state.current_sid, "exploration")
        
        if status == "✅ Complete":
            st.success("✅ Exploration completed!")
            
            # Show insights
            eval_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "evaluation"
            insights_dir = eval_dir / "insights"
            
            if insights_dir.exists():
                manifest_files = list(insights_dir.glob("*manifest*.json"))
                if manifest_files:
                    manifest = json.loads(manifest_files[0].read_text())
                    
                    # Show explanation
                    expl = manifest.get("explanation", {})
                    md_path = expl.get("md")
                    
                    if md_path and Path(md_path).exists():
                        st.markdown("####  Executive Summary")
                        with st.container():
                            st.markdown(Path(md_path).read_text(encoding="utf-8"))
                    
                    # Show visualizations
                    visuals = manifest.get("visuals", [])
                    if visuals:
                        st.markdown("####  Process Analytics")
                        cols = st.columns(2)
                        for i, vp in enumerate(visuals):
                            if Path(vp).exists():
                                with cols[i % 2]:
                                    st.image(str(Path(vp)), use_column_width=True)
        
        else:
            control_dir = st.session_state.sdl.sdl_root / st.session_state.current_sid / "control"
            
            if (control_dir / "cmd_exploration_start.json").exists():
                if st.button("▶️ Run Exploration", type="primary", use_container_width=True):
                    with st.spinner(" Generating insights and visualizations..."):
                        infra = ExplorationInfra(st.session_state.sdl)
                        res = infra.run(st.session_state.current_sid)
                        
                        st.success("✅ Exploration completed!")
                        (control_dir / "cmd_exploration_start.json").unlink()
                        st.rerun()
    
    # Tab 5: Decision
    with tabs[5]:
        st.markdown("### ✅ Final Decision")
        
        exploration_complete = get_phase_status(st.session_state.current_sid, "exploration") == "✅ Complete"
        
        if not exploration_complete:
            st.warning("⚠️ Please complete the Exploration phase first.")
        else:
            # Decision form with better UI
            with st.form("decision_form"):
                st.markdown("####  Select Your Decision")
                
                decision_options = {
                    "approve": {"label": "✅ Approve", "desc": "Proceed with implementation", "type": "success"},
                    "request_changes": {"label": " Request Changes", "desc": "Modify TO-BE process", "type": "warning"},
                    "re_simulate": {"label": " Re-simulate", "desc": "Adjust parameters", "type": "info"},
                    "rescope": {"label": " Rescope", "desc": "Redefine scope", "type": "error"}
                }
                
                cols = st.columns(4)
                decision_type = None
                
                for i, (key, opt) in enumerate(decision_options.items()):
                    with cols[i]:
                        if st.button(opt["label"], use_container_width=True):
                            decision_type = key
                            st.session_state.decision_type = key
                
                # Use session state for decision
                if 'decision_type' in st.session_state:
                    decision_type = st.session_state.decision_type
                    opt = decision_options[decision_type]
                    
                    if opt["type"] == "success":
                        st.success(f"Selected: {opt['label']} - {opt['desc']}")
                    elif opt["type"] == "warning":
                        st.warning(f"Selected: {opt['label']} - {opt['desc']}")
                    elif opt["type"] == "info":
                        st.info(f"Selected: {opt['label']} - {opt['desc']}")
                    else:
                        st.error(f"Selected: {opt['label']} - {opt['desc']}")
                
                st.markdown("#### Provide Rationale")
                rationale = st.text_area("Explain your decision:", 
                                       placeholder="Why did you make this decision? What are the key factors?",
                                       height=100)
                
                # Change request section (if needed)
                change_request = {}
                if decision_type in ["request_changes", "re_simulate"]:
                    with st.expander(" Specify Changes", expanded=True):
                        change_type = st.selectbox("Change Type:", 
                                                 ["", "Modify Timer", "Rename Task", "Disable Suggestion"])
                        
                        if change_type == "Modify Timer":
                            col1, col2 = st.columns(2)
                            with col1:
                                target = st.text_input("Timer ID:")
                            with col2:
                                duration = st.text_input("New Duration:", placeholder="e.g., PT48H")
                            
                            if target and duration:
                                change_request = {
                                    "apply": [{
                                        "action": "update_timer",
                                        "target": target,
                                        "duration": duration
                                    }]
                                }
                
                submit = st.form_submit_button("💾 Submit Decision", type="primary", use_container_width=True)
                
                if submit and decision_type:
                    fc = FeedbackCollector(st.session_state.sdl)
                    res = fc.submit(
                        sid=st.session_state.current_sid,
                        decision_type=decision_type,
                        change_request=change_request,
                        rationale=rationale
                    )
                    
                    if res.get("status") == "success":
                        st.success("✅ Decision submitted successfully!")
                        st.session_state.orchestrator.handle_event(res.get("event"))
                        
                        # Show next steps with nice formatting
                        if decision_type == "approve":
                            st.balloons()
                            st.markdown("""
                            ### 🎉 Congratulations!
                            
                            Your optimized process has been approved for implementation.
                            
                            **Next Steps:**
                            1. Download the TO-BE BPMN model
                            2. Configure your BPM system
                            3. Train your team
                            4. Monitor the implementation
                            """)
                        
                        # Clear decision from session state
                        if 'decision_type' in st.session_state:
                            del st.session_state.decision_type
                    else:
                        st.error(f"Error: {res.get('error')}")

def run_pipeline_now(sid):
    sdl = st.session_state.sdl

    # 1) INTERPRETATION
    interp = ISTProcessInterpreterAgent(sdl)
    res = interp.run(sid)  # passe an deine Signatur an
    as_is_bpmn = res.get("as_is_bpmn") or res.get("bpmn_xml_path")
    qa = res.get("quality") or {}
    _write_gate(sdl, sid, "interpretation",
                payload={"quality": qa}, 
                artefacts={"as_is_bpmn": as_is_bpmn},
                event_type="interpretation.done")

    # 2) GENERATION (Modeling)
    modeler = ImprovedModelingAgent(sdl)
    gen = modeler.run(sid)  # passe an deine Signatur an
    tobe_bpmn = gen.get("tobe_bpmn")
    _write_gate(sdl, sid, "generation",
                payload={"applied_count": gen.get("applied_count", 0),
                         "validation_warnings": gen.get("validation_warnings", [])},
                artefacts={"tobe_bpmn": tobe_bpmn},
                event_type="generation.done")

    # 3) EVALUATION
    engine = EvaluationEngine(sdl)
    eval_res = engine.run(sid)  # schreibt evaluation/*
    _write_gate(sdl, sid, "evaluation",
                payload={"summary": eval_res.get("summary")},
                artefacts={"comparison_report": eval_res.get("comparison_report")},
                event_type="evaluation.done")

    # 4) EXPLORATION (Viz/Explain)
    infra = ExplorationInfra(sdl)
    infra.run(sid)

if st.session_state.current_sid and st.button("Run end-to-end now"):
    with st.spinner("Running full pipeline..."):
        run_pipeline_now(st.session_state.current_sid)
    st.success("Pipeline finished.")
elif not st.session_state.current_sid:
    st.info("Select or create a session first to run the full pipeline.")



# Footer
st.markdown("---")
footer_cols = st.columns([2, 1, 1])
with footer_cols[0]:
    st.markdown(" **Process AI** - Automated Business Process Optimization")
with footer_cols[1]:
    st.markdown(f"Session: `{st.session_state.current_sid or 'None'}`")
with footer_cols[2]:
    st.markdown(f"Version: 2.0.0")