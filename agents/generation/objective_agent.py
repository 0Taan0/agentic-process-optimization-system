from pathlib import Path
import json
import re
from datetime import datetime, timezone
from core.mongo_sdl import MongoSDL
from openai import OpenAI

client = OpenAI()

def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

class ObjectiveAgent:
    """
    Extrahiert Ziele & weiche Constraints für die Generation.
    Nutzt vorhandene Meta-Felder (goals/soft_constraints) aus der Session,
    sonst LLM-gestützte Extraktion aus user_prompt (strict JSON).
    """

    def __init__(self, sdl: MongoSDL):
        self.sdl = sdl

    def run(self, sid: str):
        # Session-Meta lesen
        meta = self.sdl.read_session_meta(sid)
        user_prompt = meta.get("user_prompt", "") or ""

        # Prüfen, ob Interaction-Agent schon strukturierte Ziele/Constraints geliefert hat
        goals = meta.get("goals")
        soft_constraints = meta.get("soft_constraints")

        # Falls beides fehlt -> LLM-Extraktion (strict JSON)
        if not goals or not soft_constraints:
            if not user_prompt.strip():
                # Nichts zum Extrahieren vorhanden
                goals = goals or []
                soft_constraints = soft_constraints or []
            else:
                system_prompt = (
                    "Du extrahierst aus einem Nutzerkontext **ausschließlich** JSON.\n"
                    "Gib **nur** dieses JSON zurück, keine Erklärungen, kein Fließtext, keine Codeblöcke.\n"
                    "Schema:\n"
                    "{\n"
                    '  "goals": [string, ...],\n'
                    '  "soft_constraints": [string, ...]\n'
                    "}\n"
                    "Beispiele für goals: \"lead_time↓\", \"cost↓\", \"throughput↑\".\n"
                    "soft_constraints sind weiche Regeln/Guidelines in Textform (z. B. \"maintain_quality\")."
                )
                user_msg = f"Kontext:\n{user_prompt}\n\nExtrahiere goals und soft_constraints gemäß Schema."

                # Chat Completions (kompatibel zu vielen SDK-Versionen)
                chat = client.chat.completions.create(
                    model="gpt-4o",           # ggf. anpassen
                    temperature=0.3,          # leicht explorativ, aber stabil
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                )
                content = chat.choices[0].message.content or ""
                txt = content.strip()

                # Codefences entfernen, falls das Modell trotzdem welche setzt
                if txt.startswith("```"):
                    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S).strip()

                # Hartes JSON-Parsing (keine Dummy-Fallbacks)
                obj = json.loads(txt)

                goals = obj.get("goals", [])
                soft_constraints = obj.get("soft_constraints", [])

        # Requirements-Objekt erstellen
        requirements = {
            "sid": sid,
            "created_at": _now_iso(),
            "goals": goals,
            "soft_constraints": soft_constraints,
        }

        # Persistieren über deine IO-Helper
        from agents.generation.io import (
            path_requirements, save_json_and_record, T_REQUIREMENTS
        )

        req_path = path_requirements(sid, self.sdl)
        save_json_and_record(
            self.sdl,
            sid,
            T_REQUIREMENTS,
            req_path,
            requirements,
            summary={
                "goals": requirements["goals"],
                "soft_constraints_count": len(requirements["soft_constraints"])
            },
            overwrite=True  # oder False, wenn du Idempotenz willst
        )
        return {"requirements_path": str(req_path), "requirements": requirements}
 