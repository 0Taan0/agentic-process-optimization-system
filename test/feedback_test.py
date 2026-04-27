from agents.feedback.exploration_infra import ExplorationInfra
from agents.feedback.feedback_collector import FeedbackCollector
from core.shared_data_layer import SharedDataLayer

sid = "<deine_sid>"
sdl = SharedDataLayer(base_dir=r"C:\DEV\MA\data\sdl")

# 1) Exploration ausführen
infra = ExplorationInfra(sdl)
infra.run(sid)  # schreibt insights_manifest

# 2) Collector zieht Manifest
fc = FeedbackCollector(sdl)
print(fc.prepare(sid))  # zeigt Pfade & Issues

# 3) Entscheidung abgeben (z.B. approve)
res = fc.submit(sid, "approve", rationale="Looks good.")
print(res["event"])     # -> {"type":"decision.done", ...}
