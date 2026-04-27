from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
from datetime import datetime, timezone
import csv
import io
import json
import logging
import xml.etree.ElementTree as ET
import sys
import gzip
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from dateutil import parser as dateparser
except Exception:
    dateparser = None  # wird unten mit Fallback gehandhabt


UploadPathOrBytes = Union[Path, Tuple[str, bytes]]

logger = logging.getLogger(__name__)



# Hilfsfunktionen (Zeit)

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_ts(value: str) -> Optional[str]:
    """
    Timestamp robust nach ISO 8601 (UTC) normalisieren.
    Gibt ISO-String oder None zurück.
    """
    if not value:
        return None
    value = value.strip()
    # 1) Mit dateutil wenn vorhanden
    if dateparser:
        try:
            dt = dateparser.parse(value)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.replace(microsecond=0).isoformat()
        except Exception:
            return None
    # 2) Fallback: wenige gängige Formate
    fmts = [
        "%Y-%m-%dT%H:%M:%S%z",  # 2024-11-05T09:12:33+00:00
        "%Y-%m-%dT%H:%M:%S",    # 2024-11-05T09:12:33 (ohne TZ)
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(value, fmt)
            # ohne TZ → UTC annehmen
            if fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.replace(microsecond=0).isoformat()
        except Exception:
            continue
    return None


def _detect_source(uploads: Sequence[UploadPathOrBytes]) -> Dict[str, Any]:
    """
    Wählt eine Quelle (Priorität: XES > CSV). BPMN wird nicht als Eventquelle genutzt.
    Rückgabe: {"type": "xes"|"csv"|"none", "obj": Path | (name, bytes)}
    """
    for u in uploads:
        if isinstance(u, Path):
            # Check for .xes or .xes.gz
            if u.suffix.lower() == ".xes" or (u.suffixes and u.suffixes[-2:] == ['.xes', '.gz']):
                return {"type": "xes", "obj": u}
    
    csvp = next((u for u in uploads if isinstance(u, Path) and u.suffix.lower() == ".csv"), None)
    if csvp:
        return {"type": "csv", "obj": csvp}

    # Bytes-Varianten
    for u in uploads:
        if isinstance(u, tuple):
            name = u[0].lower()
            if name.endswith(".xes") or name.endswith(".xes.gz"):
                return {"type": "xes", "obj": u}
    
    csv_b = next((u for u in uploads if isinstance(u, tuple) and u[0].lower().endswith(".csv")), None)
    if csv_b:
        return {"type": "csv", "obj": csv_b}

    return {"type": "none", "obj": None}


# CSV / XES einlesen
CSV_CASE_KEYS = {"case_id", "case", "order_id", "trace_id"}
CSV_ACT_KEYS = {"activity", "event", "task", "name", "activity_name"}
CSV_TS_KEYS = {"timestamp", "time", "datetime", "event_time" }
CSV_RES_KEYS = {"resource", "user", "performer", "agent"}

def _open_csv(obj: Union[Path, Tuple[str, bytes]]) -> Iterable[Dict[str, Any]]:
    """
    Gibt Dict-Zeilen (Spalten→Wert) zurück.
    """
    if isinstance(obj, Path):
        f = obj.open("r", encoding="utf-8-sig", newline="")
        return csv.DictReader(f)
    # bytes
    name, blob = obj
    stream = io.StringIO(blob.decode("utf-8-sig"))
    return csv.DictReader(stream)


def _csv_to_raw_events(obj: Union[Path, Tuple[str, bytes]]) -> List[Dict[str, Any]]:
    rows = []
    reader = _open_csv(obj)
    for row in reader:
        # alle Keys normalisieren (lower)
        norm = { (k or "").strip().lower(): (v if v is not None else "") for k, v in row.items() }
        rows.append(norm)
    return rows


def _xes_to_raw_events(path_or_bytes: Union[Path, Tuple[str, bytes]]) -> List[Dict[str, Any]]:
    """
    Einfacher XES-Reader (minimale Unterstützung).
    Erwartet Standard-XES-Struktur. Extrahiert mindestens:
    - case_id (trace id oder concept:name in trace)
    - activity (string attr concept:name in event)
    - timestamp (date attr time:timestamp in event)
    - resource (org:resource optional)
    - andere string/number-Attribute landen in attributes{}
    """
    # Parse XML
    '''if isinstance(path_or_bytes, Path):
        tree = ET.parse(str(path_or_bytes))
    else:
        _, blob = path_or_bytes
        tree = ET.ElementTree(ET.fromstring(blob))'''
        # Parse XML
    if isinstance(path_or_bytes, Path):
        # Check if it's a .gz file
        if str(path_or_bytes).endswith('.gz'):
            import gzip
            with gzip.open(str(path_or_bytes), 'rt', encoding='utf-8') as f:
                content = f.read()
            tree = ET.ElementTree(ET.fromstring(content))
        else:
            tree = ET.parse(str(path_or_bytes))
    else:
        _, blob = path_or_bytes
        tree = ET.ElementTree(ET.fromstring(blob))

    root = tree.getroot()
    ns = {"xes": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}

    def _get_attr(elem: ET.Element, key: str) -> Optional[str]:
        for c in elem:
            k = c.attrib.get("key") or c.attrib.get("{http://www.xes-standard.org/}key")
            if k == key:
                # string/attr values
                for attr_name in ("value", "{http://www.xes-standard.org/}value"):
                    if attr_name in c.attrib:
                        return c.attrib[attr_name]
                # date
                if c.tag.endswith("date") and "value" in c.attrib:
                    return c.attrib["value"]
        return None

    events: List[Dict[str, Any]] = []

    for trace in root.findall(".//xes:trace" if ns else ".//trace", ns):
        case_id = _get_attr(trace, "concept:name") or _get_attr(trace, "case:id") or "unknown"
        for ev in trace.findall(".//xes:event" if ns else ".//event", ns):
            activity = _get_attr(ev, "concept:name")
            ts = _get_attr(ev, "time:timestamp")
            resource = _get_attr(ev, "org:resource")
            attrs: Dict[str, Any] = {}
            # Nebenattribute (minimalistisch einsammeln)
            for c in ev:
                k = c.attrib.get("key") or c.attrib.get("{http://www.xes-standard.org/}key")
                if k in ("concept:name", "time:timestamp", "org:resource"):
                    continue
                v = c.attrib.get("value") or c.attrib.get("{http://www.xes-standard.org/}value")
                if k:
                    attrs[k] = v
            events.append({
                "case_id": case_id,
                "activity": activity or "",
                "timestamp": ts or "",
                "resource": resource or "",
                "attributes": attrs
            })
    return events

# Normalisierung + DQ

def _normalize_csv_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    CSV-Zeilen → kanonische Events. Liefert (events, dq_counters)
    dq_counters trackt einfache Qualitätszahlen.
    """
    events: List[Dict[str, Any]] = []
    dq = dict(missing_case=0, missing_act=0, missing_ts=0, bad_ts=0, total=0)

    # Spalten-Mapping bestimmen
    def _first_key(cands: Iterable[str], rowkeys: Iterable[str]) -> Optional[str]:
        rowset = set(rowkeys)
        for c in cands:
            if c in rowset:
                return c
        return None

    if rows:
        keys = rows[0].keys()
        k_case = _first_key(CSV_CASE_KEYS, keys)
        k_act  = _first_key(CSV_ACT_KEYS, keys)
        k_ts   = _first_key(CSV_TS_KEYS, keys)
        k_res  = _first_key(CSV_RES_KEYS, keys)
    else:
        k_case = k_act = k_ts = k_res = None

    for r in rows:
        dq["total"] += 1
        case_id = (r.get(k_case, "") if k_case else "").strip()
        activity = (r.get(k_act, "") if k_act else "").strip()
        ts_raw = (r.get(k_ts, "") if k_ts else "").strip()
        resource = (r.get(k_res, "") if k_res else "").strip() if k_res else ""

        if not case_id: dq["missing_case"] += 1
        if not activity: dq["missing_act"] += 1
        if not ts_raw: dq["missing_ts"] += 1

        ts_norm = _parse_ts(ts_raw) if ts_raw else None
        if ts_raw and not ts_norm:
            dq["bad_ts"] += 1

        # übrige Spalten → attributes
        attributes = {k: v for k, v in r.items() if k not in {k_case, k_act, k_ts, k_res} and k is not None}

        evt = {
            "case_id": case_id,
            "activity": activity,
            "timestamp": ts_norm or "",
            "resource": resource,
            "attributes": attributes
        }
        events.append(evt)

    return events, dq


def _normalize_xes_events(raw: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    events: List[Dict[str, Any]] = []
    dq = dict(missing_case=0, missing_act=0, missing_ts=0, bad_ts=0, total=0)
    for ev in raw:
        dq["total"] += 1
        case_id = (ev.get("case_id") or "").strip()
        activity = (ev.get("activity") or "").strip()
        ts_raw = (ev.get("timestamp") or "").strip()
        resource = (ev.get("resource") or "").strip()
        attrs = ev.get("attributes") or {}

        if not case_id: dq["missing_case"] += 1
        if not activity: dq["missing_act"] += 1
        if not ts_raw: dq["missing_ts"] += 1

        ts_norm = _parse_ts(ts_raw) if ts_raw else None
        if ts_raw and not ts_norm:
            dq["bad_ts"] += 1

        events.append({
            "case_id": case_id,
            "activity": activity,
            "timestamp": ts_norm or "",
            "resource": resource,
            "attributes": attrs
        })
    return events, dq


def _compute_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    cases = {e["case_id"] for e in events if e.get("case_id")}
    acts  = {e["activity"] for e in events if e.get("activity")}
    ts_vals = [e["timestamp"] for e in events if e.get("timestamp")]
    ts_vals = [t for t in ts_vals if t]  # keine leeren
    span = {}
    if ts_vals:
        span = {"min_ts": min(ts_vals), "max_ts": max(ts_vals)}
    return {
        "cases": len(cases),
        "events": len(events),
        "activities": len(acts),
        **span
    }



# Artefakte schreiben
def _resolve_perception_dir(sdl: Any, sid: str) -> Path:
    """
    Versucht, den Session-Ordner aus dem SharedDataLayer zu bestimmen.
    Erwartet entweder sdl.base_dir oder sdl.get_session_dir(sid).
    Fällt ansonsten auf ./data/sdl/<sid>/perception zurück.
    """
    # 1) sdl.get_session_dir()
    if hasattr(sdl, "get_session_dir"):
        base = Path(getattr(sdl, "get_session_dir")(sid))
        pdir = base / "perception"
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir
    # 2) sdl.base_dir
    if hasattr(sdl, "base_dir"):
        base = Path(getattr(sdl, "base_dir")) / sid / "perception"
        base.mkdir(parents=True, exist_ok=True)
        return base
    # 3) Fallback
    base = Path("data") / "sdl" / sid / "perception"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _export_xes_simple(events: List[Dict[str, Any]], out_path: Path) -> None:
    """
    Sehr einfache XES-Erzeugung: group by case_id → trace, schreibe wenige Felder.
    Für Tools/Next-Phasen als Interim ausreichend.
    """
    traces: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        traces.setdefault(e["case_id"] or "unknown", []).append(e)

    log = ET.Element("log")
    for cid, evs in traces.items():
        t = ET.SubElement(log, "trace")
        name = ET.SubElement(t, "string", key="concept:name", value=str(cid))
        # Events
        for ev in evs:
            e = ET.SubElement(t, "event")
            ET.SubElement(e, "string", key="concept:name", value=ev.get("activity",""))
            if ev.get("resource"):
                ET.SubElement(e, "string", key="org:resource", value=ev["resource"])
            if ev.get("timestamp"):
                ET.SubElement(e, "date", key="time:timestamp", value=ev["timestamp"])
            # attributes (flach)
            attrs = ev.get("attributes") or {}
            for k, v in attrs.items():
                ET.SubElement(e, "string", key=str(k), value=str(v))

    tree = ET.ElementTree(log)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(out_path), encoding="utf-8", xml_declaration=True)



# Öffentliche API (Direkt)

def run_integration_direct(
    sid: str,
    meta: Dict[str, Any],
    uploads: Sequence[UploadPathOrBytes],
    *,
    sdl: Any  # SharedDataLayer-Instanz (für Pfade/Ablage)
) -> Dict[str, Any]:
    """
    Orchestriert die Integration:
    - Quelle erkennen
    - Parsen
    - Normalisieren
    - DQ berechnen
    - Artefakte schreiben (JSON, optional XES)
    - Zusammenfassung zurückgeben
    """
    logger.info("integration.start sid=%s", sid)

    # 1) Quelle
    src = _detect_source(uploads)
    if src["type"] == "none":
        logger.warning("integration.no_source sid=%s", sid)
        # leeren Report schreiben
        pdir = _resolve_perception_dir(sdl, sid)
        dq_report = {
            "sid": sid,
            "created_at": _now_iso(),
            "qc_pass": False,
            "issues": ["no_event_source_found"],
            "stats": {"cases": 0, "events": 0, "activities": 0}
        }
        dq_path = pdir / f"dq_report_{sid}.json"
        _write_json(dq_path, dq_report)
        return {
            "integrated_path": None,
            "clean_xes_path": None,
            "dq_report_path": str(dq_path),
            "qc_pass": False,
            "stats": dq_report["stats"]
        }

    # 2) Parsen → raw events
    if src["type"] == "csv":
        raw = _csv_to_raw_events(src["obj"])
        events, dq_cnt = _normalize_csv_rows(raw)
    else:  # xes
        raw = _xes_to_raw_events(src["obj"])
        events, dq_cnt = _normalize_xes_events(raw)

    # 3) Stats + QC
    stats = _compute_stats(events)
    qc_pass = stats["events"] > 0 and (dq_cnt["missing_case"] + dq_cnt["missing_act"] + dq_cnt["missing_ts"]) < stats["events"]

    # 4) Artefakte schreiben
    pdir = _resolve_perception_dir(sdl, sid)

    integrated = {
        "sid": sid,
        "created_at": _now_iso(),
        "stats": stats,
        "events": events
    }
    integrated_path = pdir / f"integrated_eventlog_{sid}.json"
    _write_json(integrated_path, integrated)

    # Mongo-Spiegel: integrated_eventlog
    if hasattr(sdl, "record_artefact"):
        sdl.record_artefact(
            sid, "perception", "integrated_eventlog", integrated_path,
            summary=stats
        )


    clean_xes_path: Optional[Path] = None

    if src["type"] == "csv":
        # CSV -> XES export
        tmp_clean = pdir / f"clean_xes_{sid}.xes"
        try:
            _export_xes_simple(events, tmp_clean)
        except Exception as e:
            logger.exception("integration.xes_export_failed sid=%s err=%s", sid, e)
            tmp_clean = None
        if tmp_clean and tmp_clean.exists():
            clean_xes_path = sdl.save_clean_xes(sid, tmp_clean)

    elif src["type"] == "xes":
        # falls Upload .xes.gz ist -> entpacken und als clean_xes ablegen
        obj = src["obj"]
        tmp_clean = pdir / f"clean_xes_{sid}.xes"
        try:
            if isinstance(obj, Path) and str(obj).lower().endswith(".xes.gz"):
                import gzip
                with gzip.open(obj, "rb") as f_in:
                    tmp_clean.write_bytes(f_in.read())
                if tmp_clean.exists():
                    clean_xes_path = sdl.save_clean_xes(sid, tmp_clean)
            elif isinstance(obj, tuple) and obj[0].lower().endswith(".xes.gz"):
                import gzip, io
                name, blob = obj
                with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gz:
                    tmp_clean.write_bytes(gz.read())
                if tmp_clean.exists():
                    clean_xes_path = sdl.save_clean_xes(sid, tmp_clean)
        except Exception as e:
            logger.exception("integration.gz_unpack_failed sid=%s err=%s", sid, e)



    dq_report = {
        "sid": sid,
        "created_at": _now_iso(),
        "qc_pass": qc_pass,
        "issues": [
            k for k, v in dq_cnt.items()
            if k in {"missing_case", "missing_act", "missing_ts", "bad_ts"} and v > 0
        ],
        "counters": dq_cnt,
        "stats": stats,
        "source_type": src["type"]
    }
    dq_path = sdl.save_dq_report(sid, dq_report)

    logger.info("integration.done sid=%s events=%d qc_pass=%s", sid, stats["events"], qc_pass)

    
    # Gate1 am ende  perception.done schreiben 
    from control.gates import write_gate_event

    artefacts = {
        "integrated_events": str(integrated_path),
        "clean_xes": str(clean_xes_path),
        "dq_report": str(dq_path),
    }
    payload = {
        "dq_status": "GREEN" if qc_pass else "RED",
        "stats": stats
    }

    # Dynamisch .done oder .fail setzen /Gate event schrieben
    event_type = "perception" + (".done" if qc_pass else ".fail")
    gate_name  = "perception"

    gate_path, event_dict = write_gate_event(
        sdl=sdl,
        sid=sid,
        gate=gate_name,
        payload=payload,
        artefacts=artefacts,
        event_type=event_type
    )


    # Logging/Console – ok vor return
    logger.info("integration.perception_done sid=%s gate_path=%s", sid, gate_path)
    print(f"[Gate1] perception.done geschrieben: {gate_path}")

    return {
        "integrated_path": str(integrated_path),
        "clean_xes_path": str(clean_xes_path) if clean_xes_path else None,
        "dq_report_path": str(dq_path),
        "qc_pass": qc_pass,
        "stats": stats
    }


def run_integration_with_process_mining(
    sid: str,
    meta: Dict[str, Any],
    uploads: Sequence[UploadPathOrBytes],
    process_mining_result: Dict[str, Any],
    *,
    sdl: Any
) -> Dict[str, Any]:
    """
    Modified integration that includes process mining results in quality assessment
    """
    logger.info("integration.start sid=%s with_process_mining=%s", sid, bool(process_mining_result))
    
    result = run_integration_direct(sid, meta, uploads, sdl=sdl)
    

    if process_mining_result and process_mining_result.get("status") == "success":

        dq_report_path = Path(result["dq_report_path"])
        if dq_report_path.exists():
            dq_report = json.loads(dq_report_path.read_text())
            dq_report["process_mining"] = {
                "status": "success",
                "ist_bpmn_discovered": True,
                "activities": process_mining_result["statistics"]["activities"],
                "cases": process_mining_result["statistics"]["cases"]
            }
            

            if process_mining_result["statistics"]["activities"] > 0:
                dq_report["qc_pass"] = True
                result["qc_pass"] = True
            
            _write_json(dq_report_path, dq_report)
    
    _trigger_perception_complete(sid, result, process_mining_result, sdl)
    
    return result

def _trigger_perception_complete(sid: str, integration_result: Dict, pm_result: Dict, sdl: Any):
    """Trigger perception.done with combined results"""
    from control.gates import write_gate_event
    
    artefacts = {
        "integrated_events": integration_result.get("integrated_path"),
        "dq_report": integration_result.get("dq_report_path"),
    }
    
    if integration_result.get("clean_xes_path"):
        artefacts["clean_xes"] = integration_result["clean_xes_path"]
    
    if pm_result and pm_result.get("status") == "success":
        artefacts["discovered_bpmn"] = pm_result["ist_bpmn_path"]
        artefacts["performance_data"] = pm_result["performance_data_path"]
    
    qc_pass = integration_result.get("qc_pass", False)
    pm_success = pm_result and pm_result.get("status") == "success"
    
    payload = {
        "dq_status": "GREEN" if qc_pass else "YELLOW" if pm_success else "RED",
        "process_mining": "SUCCESS" if pm_success else "FAILED",
        "stats": integration_result.get("stats", {})
    }
    
    event_type = "perception.done" if (qc_pass or pm_success) else "perception.fail"
    
    gate_path = write_gate_event(
        sdl=sdl,
        sid=sid,
        gate="perception",
        payload=payload,
        artefacts=artefacts,
        event_type=event_type
    )
    
    logger.info("perception.complete sid=%s gate_path=%s", sid, gate_path)

