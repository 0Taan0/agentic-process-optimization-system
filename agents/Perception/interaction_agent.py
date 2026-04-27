from __future__ import annotations
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union, Dict, Any
from datetime import datetime
import logging
import mimetypes
import os
from dotenv import load_dotenv
load_dotenv()
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from core.mongo_sdl import MongoSDL


logger = logging.getLogger(__name__)


UploadType = Union[Path, bytes]

from datetime import datetime, timezone
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()



def _collect_upload_meta(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    meta: List[Dict[str, Any]] = []
    for p in paths:
        size = p.stat().st_size if p.exists() else None
        mime, _ = mimetypes.guess_type(p.name)
        meta.append(
            {
                "filename": p.name,
                "path": str(p),
                "size_bytes": size,
                "mime": mime or "application/octet-stream",
            }
        )
    return meta


def run_interaction(
    user_prompt: str,
    uploads: Optional[Sequence[UploadType]] = None,
    upload_names: Optional[Sequence[str]] = None,
    *,
    user_id: Optional[str] = None,
    source: str = "ui",
    sdl: Optional[SharedDataLayer] = None,
) -> Tuple[str, Dict[str, Any], List[Path]]:
    """
    Einstieg der Perception-Phase.
    - Legt eine neue Session im Shared Data Layer an
    - Persistiert Session-Metadaten (Prompt, Quelle, Zeitstempel)
    - Speichert optionale Uploads (Paths oder Bytes)
    - Aktualisiert Meta mit Upload-Liste
    - Gibt (sid, meta, uploaded_paths) zurück

    Parameters
    ----------
    user_prompt : str
        Freitext-Kontext/Problemstellung vom Nutzenden (kann leer sein).
    uploads : Optional[Sequence[Union[Path, bytes]]]
        Datei-Uploads als Pfade oder Bytes.
    upload_names : Optional[Sequence[str]]
        Dateinamen, nur nötig für Einträge in `uploads`, die als Bytes übergeben werden.
    user_id : Optional[str]
        Optionaler User-Identifier für die Session-Metadaten.
    source : str
        Quelle der Interaktion, z.B. "ui", "api", "cli".
    sdl : Optional[SharedDataLayer]
        Optional injizierter SharedDataLayer (für Tests). Wenn None, wird ein neuer erstellt.

    Returns
    -------
    (sid, meta, uploaded_paths)
    """
    if sdl is None:
        sdl = MongoSDL()

    uploads = uploads or []
    upload_names = upload_names or []

    if any(isinstance(u, bytes) for u in uploads) and not upload_names:
        raise ValueError(
            "Für Byte-Uploads müssen passende Dateinamen in `upload_names` übergeben werden."
        )

    # 1) Session anlegen mit initialem Meta
    base_meta: Dict[str, Any] = {
        "created_at": _now_iso(),
        "source": source,
        "user_id": user_id,
        "user_prompt": user_prompt or "",
        "upload_count": 0,
        "uploads": [],
        "notes": [],
    }

    logger.info("interaction.start source=%s user_id=%s", source, user_id)
    sid = sdl.create_session(meta=base_meta)
    logger.info("interaction.session_created sid=%s", sid)

    # 2) Uploads speichern
    saved_paths: List[Path] = []
    byte_name_idx = 0

    for item in uploads:
        if isinstance(item, Path):
            if not item.exists():
                logger.warning("interaction.upload_path_missing path=%s", str(item))
                base_meta["notes"].append(f"Upload missing: {str(item)}")
                continue
            saved = sdl.save_upload_file(sid, item)
            saved_paths.append(saved)
            logger.info(
                "interaction.upload_saved sid=%s filename=%s size=%s",
                sid,
                saved.name,
                saved.stat().st_size,
            )
        else:
            # bytes
            try:
                name = upload_names[byte_name_idx]
            except IndexError:
                raise ValueError(
                    "Anzahl der `upload_names` reicht nicht für Byte-Uploads aus."
                )
            byte_name_idx += 1
            # Fallback-Name, wenn leer
            filename = name or f"upload_{byte_name_idx}"
            saved = sdl.save_upload_bytes(sid, filename, item)
            saved_paths.append(saved)
            try:
                size = os.path.getsize(saved)
            except OSError:
                size = None
            logger.info(
                "interaction.upload_saved sid=%s filename=%s size=%s",
                sid,
                saved.name,
                size,
            )

    # 3) Meta aktualisieren
    upload_meta = _collect_upload_meta(saved_paths)
    base_meta["uploads"] = upload_meta
    base_meta["upload_count"] = len(upload_meta)
    base_meta["updated_at"] = _now_iso()
    sdl.save_session_meta(sid, base_meta)
    logger.info(
        "interaction.done sid=%s upload_count=%d",
        sid,
        base_meta["upload_count"],
    )

    return sid, base_meta, saved_paths




'''
LLM Aufrufgenerierung:
'''

from openai import OpenAI
import os
from crewai import Agent, Task, Crew, LLM
# Keys/Endpoint aus ENV
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise EnvironmentError("OPENAI_API_KEY fehlt (in .env setzen).")

gpt5mini_kwargs = {
    "model": "gpt-5-mini",
    "api_key": OPENAI_API_KEY,
}


gpt5mini_llm = LLM(**gpt5mini_kwargs)
# Feature-Flag
USE_LLM_ANALYSIS = True
def analyze_prompt_with_llm(user_prompt: str) -> dict:
    """Analyze the user prompt with an LLM via CrewAI and return structured information."""
    if not USE_LLM_ANALYSIS:
        return {}

    analysis_agent = Agent(
        role="Business Process Intake Analyst",
        goal="Analyze and classify user input related to business process improvement.",
        backstory=(
            "You are an expert in business process analysis. "
            "Your task is to interpret the user's description, "
            "identify the affected process domain, "
            "determine the type of issue, "
            "assess the urgency, and detect any missing information. "
            "Provide the result as structured JSON."
        ),
        verbose=True,
        allow_delegation=False,
        llm=gpt5mini_llm 
    )

    analysis_task = Task(
        description=(
            f"Analysiere folgenden Nutzer-Input:\n\n"
            f"'''{user_prompt}'''\n\n"
            "Gib das Ergebnis als JSON mit den Feldern "
            "{domain, issue_type, urgency, missing_fields[]} zurück."
        ),
        agent=analysis_agent,
        expected_output="Ein valides JSON-Objekt."
    )

    crew = Crew(agents=[analysis_agent], tasks=[analysis_task])
    result = crew.kickoff()

    try:
        import json
        parsed = json.loads(result)
        return parsed
    except Exception:
        return {"raw_output": result}
    

if __name__ == "__main__":
    from core.shared_data_layer import SharedDataLayer

    prompt = input("Bitte Problem/Kontext eingeben: ")

    sdl = SharedDataLayer()
    sid, meta, paths = run_interaction(
        user_prompt=prompt,
        uploads=[],
        sdl=sdl
    )
    print("Session ID:", sid)
    print("Meta:", meta)
    print("Upload Paths:", paths)

    if USE_LLM_ANALYSIS:
        analysis = analyze_prompt_with_llm(meta["user_prompt"])
        print("LLM Analysis:", analysis)
        sdl.save_dq_report(sid, analysis)
