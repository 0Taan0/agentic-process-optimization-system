MAS – Multi-Agent System für automatisierte Soll-Prozessvorschläge (BPMN)
Überblick

Dieses Projekt generiert aus realen Eventlogs (XES) automatisierte Soll-Prozessmodelle (To-Be) in BPMN 2.0.
Der End-to-End-Ablauf: Perception → Interpretation → Generation → Evaluation → Exploration/Feedback – optional mit einer zweiten Iteration (v2) als Feinschliff.

Kernfeatures

Import & Bereinigung von XES (u. a. BPI-Challenges) mit PM4Py

Mining-basiertes As-Is BPMN + BPMN-DI (Shapes/Edges/Waypoints)

LLM-gestützte Verbesserungsvorschläge (kontext-/regelbasiert)

Deterministischer Modeling-Executor (Hybrid): sichere, reproduzierbare BPMN-Transformationen

Simulation & Baseline-KPIs (Mean, P50, P90) + Conformance-Aspekte

Comparison (As-Is vs To-Be) & Priorisierung (Executive Summary, Backlog)

Feedback-Loop: approve | request_changes | re_simulate | rescope

Projektstruktur (wichtigste Module)

main.py – Startpunkt & Orchestrierungsschleife

orchestrator.py – Event-/Command-Routing (Phasenlogik)

ingestion.py / process_mining_component.py – XES-Import, PM4Py-Mining, DQ

ist_process_interpreter_agent.py – As-Is BPMN + BPMN-DI

agents/generation/

improved_generator_agent.py – LLM-Vorschläge (Planner/Jobs/Refiner-Prompting)

improved_modeling_agent.py – deterministischer BPMN-Executor (merge/automate/parallel/monitoring/…)

infra.py – Laufzeit-Infra (Constraints, Pfade, Normalisierung)

agents/evaluation/

engine.py – Baseline, Simulation, Comparison, (optionale) Priorisierung

simulation.py – KPIs, XOR-Routing, To-Be-Evaluation

comparison.py – KPI-Deltas & Empfehlung

improved_evaluation_prioritization_agent.py – Executive Summary, Backlog

agents/feedback/

exploration_infra.py – Visuals/Explainability

feedback_collector.py – Entscheidungen (approve / request_changes / re_simulate / rescope)




Voraussetzungen (siehe Requirements.txt)

Python 3.12.4 (empfohlen: venv/Conda)

Pip-Pakete (Auszug): pm4py, lxml, pandas, numpy, networkx, matplotlib, tqdm, python-dotenv, openai (bzw. dein LLM-Client)

LLM-Zugang (z. B. OpenAI): Umgebungsvariable setzen

Windows (PowerShell): $env:OPENAI_API_KEY="..."

Installiere alle Abhängigkeiten:
pip install -r requirements.txt

Daten (XES) vorbereiten (Nur wenn ein Beispiel genutzt werden soll ohne die bereits angegebene und in der Thesis genutzte Datei ansonsten nur API-Key und Main start erforderlich)

Lege XES-/XES.GZ-Dateien in einen gut erreichbaren Ordner (z. B. data/bpi_xes/) und abändern in Process_mining/main/Interpreter. Die Beispiel XES genutzt in der Thesis ist bereits schon angegeben in allen Dateien und zu sehen in BPI_xes beim Start des Prozesses wird diese Datei genutzt.

Das System erstellt beim Lauf eine Session (SID) inkl. Artefakt-Ordner:
data/sdl/<SID>/ mit Unterordnern uploads/, perception/, interpretation/, generation/, evaluation/, control/.

Tipp: Wenn du ohne UI arbeitest, kopiere die XES vor dem Start nach
data/sdl/<neue SID>/uploads/yourlog.xes(.gz) – der Ingestion-Step findet sie dort.
(Alternativ kannst du in ingestion.py eine Default-Datei konfigurieren.)







Starten

Schnellstart (CLI):

python main.py





Du wirst nach einem Business Context gefragt (z. B. englischer Kurztext).

Der Run erzeugt automatisch eine neue SID und läuft die Phasen durch.

Typischer Ablauf

Perception: XES einlesen, bereinigen, DQ-Report, clean_xes_*.xes.

Interpretation: As-Is BPMN + DI, Modell-Repo data/models/....

Generation (v1): LLM-Vorschläge → deterministische Ausführung → tobe_bpmn_<sid>_v1_<ts>.bpmn.

Evaluation: Baseline-KPIs (aus Log), To-Be-Simulation, Comparison-Report.

Exploration/Feedback: Visuals/Erklärungen; Entscheidung treffen (approve/request_changes/re_simulate/rescope).

(Optional) Generation (v2): Feinschliff, kleine Deltas, erneute Evaluation.

Artefakte (wo finde ich was?)

data/sdl/<SID>/perception/

clean_xes_<sid>.xes – bereinigtes Eventlog

dq_report.json – Datenqualitätsbericht

data/sdl/<SID>/interpretation/

ist_bpmn_<sid>.bpmn – As-Is BPMN (+ DI)

model_meta_<sid>.json

data/sdl/<SID>/generation/

tobe_bpmn_<sid>_v{n}_<ts>.bpmn – To-Be Modell (v1/v2)

tobe_meta_<sid>_v{n}_<ts>.json – angewandte Schritte/Plan

requirements_*.json, constraints_*.json, business_rules_*.json

improvement_suggestions_*_time.json – LLM-Vorschläge (mit Begründung/Confidence)

data/sdl/<SID>/evaluation/

baseline_metrics_<sid>.json – KPIs aus Log (Mean/P50/P90, per_activity, transition_counts)

sim_tobe_metrics_<sid>.json – KPIs aus Simulation des To-Be

comparison_report_<sid>.json – Delta/Empfehlung (z. B. re_iterate_generation / proceed)

ggf. executive_summary_<sid>.md, priority_backlog_<sid>.json (wenn Priorisierung läuft)

Business-Kontext (Beispiel)

Einfacher, stabiler Prompt zur Reduktion von Zyklus-/Wartezeiten (englisch empfohlen):

I want to improve the loan application process by reducing cycle time and waiting time, primarily through early automated completeness checks and the removal of avoidable manual rework. Keep the overall structure recognizable but allow a small number of targeted structural changes. Prefer converting early validation tasks into service tasks, merging back-to-back manual micro-steps owned by the same actor, and—if it accelerates the path—introducing a single non-blocking parallel branch for logging or notifications that rejoins before the next decision. Do not add pools, lanes, or subprocesses, and avoid unnecessary new events. Use only existing element IDs. Aim for a measurable reduction in end-to-end cycle time (especially p90) while keeping the model minimal, audit-ready, and free of dangling flows. Provide a short rationale for each applied change.

Wie es funktioniert (kurz)

Process Mining (PM4Py): Mining/Discovery aus XES → Struktur, Kanten, Häufigkeiten.

IST Process Interpreter: Übersetzt Mining in BPMN 2.0 + DI (visuell ladbar).

Rules Extraction Agent: Leitet aus deinem Freitext-Kontext Regeln/Constraints ab.

Improved Generator: LLM erzeugt Suggestion-Sets (JSON) mit ID-basierten Änderungen und Begründungen.

Improved Modeling Agent: Führt die Änderungen deterministisch aus (merge/automate/parallel/monitoring/…).

Refiner: Sichert vollständiges, valides BPMN-XML mit sauberem BPMN-DI.

Simulation: Berechnet Baseline (aus Log) und To-Be-KPIs (inkl. XOR-Routing aus Transition-Counts).

Comparison: Delta-KPIs + Empfehlung (z. B. re_iterate_generation für v2).

Feedback: approve | request_changes | re_simulate | rescope → triggert nächsten Schritt.

Häufige Fragen & Troubleshooting

Baseline-KPIs sind 0/None

Prüfe perception/clean_xes_*.xes existiert.

Logs enthalten lifecycle:start/complete? Falls nein, greift Trace-Fallback (first→last).

baseline_metrics_<sid>.json wird vor To-Be-Simulation geschrieben – dort nachsehen.

“re_iterate_generation” → keine Executive Summary

Comparison empfiehlt neue Generation (Verbesserung nicht signifikant).

Wenn du immer Priorisierung willst: in engine.py den Skip entfernen und Prioritization stets ausführen.

LLM “erfindet” IDs

In den Prompts auf ID-Whitelist bestehen; Modeling-Agent verwirft Steps ohne gültige IDs.

Diagramm leer im Modeler

Fehlt BPMN-DI? Der Refiner ergänzt Shapes/Edges; ggf. erneut generieren.

Konfiguration & Anpassung

Prompts (Generator/Refiner): in improved_generator_agent.py und improved_modeling_agent.py anpassbar
(z. B. Limits: max_changes, max_new_gateways; Iteration v1 „größer“, v2 „klein“).

Ressourcen/Heuristiken (Simulation): simulation.py (Automationsfaktoren, XOR-Fallbacks).

XES-Quelle: ingestion.py (Default-Pfad) oder Datei manuell unter data/sdl/<SID>/uploads/ ablegen.

Lizenz / Hinweise

Bitte beachte Lizenzbedingungen der verwendeten Datensätze (BPI-Challenge) und Bibliotheken (PM4Py, OpenAI/LLM-Provider).

Sensible Logs anonymisieren. OPENAI_API_KEY nicht einchecken.