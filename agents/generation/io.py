# agents/generation/io.py
from pathlib import Path
from typing import Any, Dict, Optional
import json
import os

from core.mongo_sdl import MongoSDL

# Artefact type constants for MongoDB
T_REQUIREMENTS = "requirements"
T_CONSTRAINTS = "constraints"
T_IMPROVEMENT_SUGGESTIONS = "improvement_suggestions"
T_IMPROVEMENT_PLAN = "improvement_plan"
T_TOBE_BPMN = "tobe_bpmn"
T_TOBE_META = "tobe_meta"
T_BUSINESS_RULES = "business_rules"

def path_requirements(sid: str, sdl: MongoSDL) -> Path:
    """Get path for requirements file"""
    return sdl.get_session_dir(sid) / "generation" / f"requirements_{sid}.json"

def path_constraints(sid: str, sdl: MongoSDL) -> Path:
    """Get path for constraints file"""
    return sdl.get_session_dir(sid) / "generation" / f"constraints_{sid}.json"

def path_business_rules(sid: str, sdl: MongoSDL) -> Path:
    """Get path for business rules file"""
    return sdl.get_session_dir(sid) / "generation" / f"business_rules_{sid}.json"

def path_improvement_suggestions(sid: str, strategy: str, sdl: MongoSDL) -> Path:
    """Get path for improvement suggestions file"""
    return sdl.get_session_dir(sid) / "generation" / f"improvement_suggestions_{sid}_{strategy}.json"

def path_improvement_plan(sid: str, sdl: MongoSDL) -> Path:
    """Get path for improvement plan file"""
    return sdl.get_session_dir(sid) / "generation" / f"improvement_plan_{sid}.json"

def path_tobe_bpmn(sid: str, sdl: MongoSDL) -> Path:
    """Get path for TO-BE BPMN file"""
    return sdl.get_session_dir(sid) / "generation" / f"tobe_bpmn_{sid}.bpmn"

def path_tobe_meta(sid: str, sdl: MongoSDL) -> Path:
    """Get path for TO-BE metadata file"""
    return sdl.get_session_dir(sid) / "generation" / f"tobe_meta_{sid}.json"

def atomic_write_text(path: Path, content: str) -> None:
    """Write text file atomically"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def save_json_and_record(
    sdl: MongoSDL,
    sid: str,
    artefact_type: str,
    path: Path,
    data: Dict[str, Any],
    summary: Optional[Dict[str, Any]] = None,
    overwrite: bool = True
) -> None:
    """
    Save JSON data to file and record in MongoDB
    
    Args:
        sdl: MongoSDL instance
        sid: Session ID
        artefact_type: Type of artefact (e.g., T_REQUIREMENTS)
        path: Path to save file
        data: Data to save
        summary: Summary for MongoDB record
        overwrite: Whether to overwrite existing file
    """
    # Check if file exists and overwrite is False
    if path.exists() and not overwrite:
        raise FileExistsError(f"File already exists and overwrite=False: {path}")
    
    # Save to filesystem
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    
    # Record in MongoDB
    sdl.record_artefact(
        sid=sid,
        phase="generation",
        artefact_type=artefact_type,
        path=path,
        summary=summary or {},
        extra={}
    )