from __future__ import annotations
import gzip
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set
import xml.etree.ElementTree as ET
from statistics import mean, median
from datetime import datetime


# XML Namespaces
XES_NS = "http://www.xes-standard.org/"
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"
DI_NS = "http://www.omg.org/spec/DD/20100524/DI"
DC_NS = "http://www.omg.org/spec/DD/20100524/DC"


def _quantile(sorted_vals: List[float], q: float) -> float:
    """Calculate quantile from sorted values."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    idx = q * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = idx - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def _safe_mean(v: List[float]) -> float:
    """Calculate mean safely."""
    return float(mean(v)) if v else 0.0


def _p50(v: List[float]) -> float:
    """Calculate 50th percentile."""
    return float(median(v)) if v else 0.0


def _p90(v: List[float]) -> float:
    """Calculate 90th percentile."""
    return _quantile(sorted(v), 0.9) if v else 0.0


@dataclass
class ActivityStats:
    """Statistics for a single activity."""
    count: int
    service_times: List[float]
    waiting_times: List[float]
    
    def agg(self) -> Dict[str, float]:
        """Aggregate statistics."""
        return {
            "count": self.count,
            "service_mean_s": _safe_mean(self.service_times),
            "service_p50_s": _p50(self.service_times),
            "service_p90_s": _p90(self.service_times),
            "waiting_mean_s": _safe_mean(self.waiting_times),
            "waiting_p50_s": _p50(self.waiting_times),
            "waiting_p90_s": _p90(self.waiting_times),
        }


@dataclass
class Node:
    """BPMN node representation."""
    id: str
    type: str
    name: str
    outgoing: List[str]
    incoming: List[str]


@dataclass
class TaskTiming:
    """Task timing statistics."""
    mean_s: float
    p50_s: float
    p90_s: float


@dataclass
class AggTiming:
    """Aggregated timing for process paths."""
    mean_s: float
    p50_s: float
    p90_s: float


def _parse_xes(path: Path) -> List[List[Dict]]:
    """Parse XES file and extract traces."""
    if str(path).lower().endswith(".gz"):
        tree = ET.parse(gzip.open(path, "rb"))
    else:
        tree = ET.parse(path)
    
    root = tree.getroot()
    ns = {"xes": XES_NS}
    traces = []
    
    for t in root.findall("xes:trace", ns):
        events = []
        for e in t.findall("xes:event", ns):
            activity = None
            lifecycle = None
            ts = None
            
            for c in e:
                tag = c.tag.rsplit("}", 1)[-1]
                key = c.attrib.get("key", "")
                val = c.attrib.get("value")
                
                if tag == "string" and key == "concept:name":
                    activity = val
                elif tag in ("date", "timestamp") or (tag == "string" and key == "time:timestamp"):
                    #Neu löschen
                    if val is None:
                        continue
                    # BPI 2017 uses different format
                    v = val.replace("Z", "+00:00").replace(" ", "T")
                    try:
                        # Try multiple formats
                        ts = datetime.fromisoformat(v).timestamp()
                    except:
                        try:
                            from dateutil import parser
                            ts = parser.parse(val).timestamp()
                        except:
                            ts = None
                elif tag == "string" and key == "lifecycle:transition":
                    lifecycle = val
            
            if activity and ts is not None:
                events.append({"activity": activity, "ts": ts, "lifecycle": lifecycle})
        
        events.sort(key=lambda ev: ev["ts"])
        if events:
            traces.append(events)
    
    return traces


def _pair_start_complete(evs: List[Dict]) -> List[Tuple[str, float]]:
    """Pair start and complete events."""
    stacks: Dict[str, List[float]] = {}
    pairs: List[Tuple[str, float]] = []
    
    if not any(e.get("lifecycle") for e in evs):
        return pairs
    
    for ev in evs:
        act = ev["activity"]
        lc = (ev.get("lifecycle") or "").lower()
        ts = ev["ts"]
        
        if "start" in lc:
            stacks.setdefault(act, []).append(ts)
        elif "complete" in lc or "end" in lc:
            st = stacks.get(act, [])
            if st:
                t0 = st.pop(0)
                pairs.append((act, ts - t0))
    
    return pairs


def compute_baseline_metrics(xes_path: Path, as_is_bpmn_path: Optional[Path] = None) -> Dict:
    """Compute baseline metrics from XES log."""
    traces = _parse_xes(Path(xes_path))
    activity_stats: Dict[str, ActivityStats] = {}
    trans_counts: Dict[Tuple[str, str], int] = {}
    cycle_times: List[float] = []
    
    from itertools import pairwise
    
    for evs in traces:
        pairs = _pair_start_complete(evs)
        
        if not pairs:
            # Estimate service/waiting times from sequence
            for a, b in pairwise(evs):
                act = a["activity"]
                delta = max(0.0, b["ts"] - a["ts"])
                service = 0.3 * delta
                waiting = 0.7 * delta
                st = activity_stats.setdefault(act, ActivityStats(0, [], []))
                st.count += 1
                st.service_times.append(service)
                st.waiting_times.append(waiting)
        else:
            # Use actual start-complete pairs
            for act, secs in pairs:
                st = activity_stats.setdefault(act, ActivityStats(0, [], []))
                st.count += 1
                st.service_times.append(max(0.0, secs))
            
            # Calculate waiting times
            for a, b in pairwise(evs):
                act = a["activity"]
                delta = max(0.0, b["ts"] - a["ts"])
                waiting = max(0.0, 0.7 * delta)
                st = activity_stats.setdefault(act, ActivityStats(0, [], []))
                st.waiting_times.append(waiting)
        
        # Count transitions
        names = [e["activity"] for e in evs]
        for a, b in pairwise(names):
            trans_counts[(a, b)] = trans_counts.get((a, b), 0) + 1
        
        # Cycle time
        if evs:
            cycle_times.append(max(0.0, evs[-1]["ts"] - evs[0]["ts"]))


    #  Fallbacks AFTER all traces processed 
    if not cycle_times and traces:
        for trace in traces[:100]:
            if len(trace) >= 2:
                duration = trace[-1]["ts"] - trace[0]["ts"]
                if duration > 0:
                    cycle_times.append(duration)

    if not cycle_times:
        cycle_times = [3600 * 24 * 7]  # 7 days

    if not activity_stats:
        activity_stats["default_activity"] = ActivityStats(1, [30.0], [10.0])

    # Aggregation
    per_activity = {k: v.agg() for k, v in activity_stats.items()}
    sorted_cycles = sorted(cycle_times)
    global_metrics = {
        "cycle_mean_s": _safe_mean(sorted_cycles),
        "cycle_p50_s": _p50(sorted_cycles),
        "cycle_p90_s": _p90(sorted_cycles),
    }
    transition_counts = {f"{a}->{b}": n for (a, b), n in trans_counts.items()}

    return {
        "per_activity": per_activity,
        "transition_counts": transition_counts,
        "global": global_metrics
    }



class BPMNGraph:
    """BPMN process graph for simulation."""
    
    def __init__(self, xml_path: Path):
        ET.register_namespace("bpmn", BPMN_NS)
        ET.register_namespace("bpmndi", BPMNDI_NS)
        ET.register_namespace("di", DI_NS)
        ET.register_namespace("dc", DC_NS)
        
        self.path = Path(xml_path)
        self.root = ET.parse(self.path).getroot()
        self.ns = {"bpmn": BPMN_NS}
        self.process = self.root.find(".//bpmn:process", self.ns)
        
        if self.process is None:
            raise ValueError("No <bpmn:process> found")
        
        self.nodes: Dict[str, Node] = {}
        self.flows: Dict[str, Tuple[str, str]] = {}
        self._index()
    
    def _index(self):
        """Index BPMN elements."""
        # First pass: collect flows
        for sf in self.process.findall("bpmn:sequenceFlow", self.ns):
            fid = sf.get("id")
            src = sf.get("sourceRef")
            tgt = sf.get("targetRef")
            if fid and src and tgt:
                self.flows[fid] = (src, tgt)
        
        # Second pass: collect nodes
        for el in list(self.process):
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "sequenceFlow":
                continue
            
            nid = el.get("id")
            name = el.get("name", "")
            if not nid:
                continue
            
            self.nodes[nid] = Node(nid, tag, name, [], [])
        
        # Third pass: build connections
        for fid, (src, tgt) in self.flows.items():
            if src in self.nodes:
                self.nodes[src].outgoing.append(fid)
            if tgt in self.nodes:
                self.nodes[tgt].incoming.append(fid)
    
    def successors(self, nid: str) -> List[str]:
        """Get successor nodes."""
        if nid not in self.nodes:
            return []
        
        succ = []
        for fid in self.nodes[nid].outgoing:
            tgt = self.flows.get(fid, (None, None))[1]
            if tgt:
                succ.append(tgt)
        return succ
    
    def start_nodes(self) -> List[str]:
        """Find start event nodes."""
        return [nid for nid, n in self.nodes.items() if n.type == "startEvent"]


def _is_automated(node: Node, el: ET.Element) -> bool:
    """Check if task is automated."""
    if node.type in ("serviceTask", "scriptTask", "businessRuleTask"):
        return True
    
    for k in el.attrib:
        lk = k.lower()
        if "implementation" in lk or "class" in lk or "delegate" in lk or "expression" in lk:
            return True
    
    return False


def _task_timing_from_baseline(name: str, base: Dict, auto: bool) -> TaskTiming:
    """Get task timing from baseline, apply automation factor."""
    pa = base.get("per_activity", {})
    s = pa.get(name)
    
    if not s:
        base_s = 30.0
        return TaskTiming(base_s, base_s * 0.9, base_s * 1.3)
    
    mean_s = float(s.get("service_mean_s", 0) + s.get("waiting_mean_s", 0))
    p50 = float(s.get("service_p50_s", 0) + s.get("waiting_p50_s", 0))
    p90 = float(s.get("service_p90_s", 0) + s.get("waiting_p90_s", 0))
    
    if auto:
        mean_s *= 0.35
        p50 *= 0.35
        p90 *= 0.50
    
    return TaskTiming(mean_s, p50, p90)


def _seq(a: AggTiming, b: AggTiming) -> AggTiming:
    """Sequential aggregation."""
    return AggTiming(a.mean_s + b.mean_s, a.p50_s + b.p50_s, a.p90_s + b.p90_s)


def _xor(branches: List[Tuple[float, AggTiming]]) -> AggTiming:
    """Exclusive OR aggregation."""
    if not branches:
        return AggTiming(0, 0, 0)
    
    m = sum(p * t.mean_s for p, t in branches)
    q50 = sum(p * t.p50_s for p, t in branches)
    q90 = sum(p * t.p90_s for p, t in branches)
    
    return AggTiming(m, q50, q90)


def _and(branches: List[AggTiming]) -> AggTiming:
    """Parallel AND aggregation."""
    if not branches:
        return AggTiming(0, 0, 0)
    
    return AggTiming(
        max(b.mean_s for b in branches),
        max(b.p50_s for b in branches),
        max(b.p90_s for b in branches)
    )

def _xor_probs(graph: BPMNGraph, node_id: str, baseline) -> Dict[str, float]:
    """Calculate XOR gateway probabilities from baseline (robust)."""
    baseline = baseline or {}

    node = graph.nodes.get(node_id)
    if not node:
        return {}

    outs = getattr(node, "outgoing", []) or []
    targets = []
    for fid in outs:
        if fid not in graph.flows:
            continue
        tgt = graph.flows[fid][1]
        if tgt not in graph.nodes:
            continue
        tname = getattr(graph.nodes[tgt], "name", None) or tgt
        targets.append((tgt, tname))

    if not targets:
        return {}

    tc_raw = (baseline.get("transition_counts") or {})
    # Normiere Keys: akzeptiere ("A","B") ODER "A->B"
    tc = {}
    for k, v in tc_raw.items():
        if isinstance(k, tuple) and len(k) == 2:
            tc[f"{k[0]}->{k[1]}"] = int(v or 0)
        else:
            tc[str(k)] = int(v or 0)

    src_name = getattr(node, "name", None) or node_id
    weights = []
    for tgt_id, tname in targets:
        key = f"{src_name}->{tname}"
        weights.append((tgt_id, tc.get(key, 0)))

    total = sum(w for _, w in weights)
    if total <= 0:
        p = 1.0 / len(targets)
        return {tgt_id: p for tgt_id, _ in targets}

    return {tgt_id: w / total for tgt_id, w in weights}


def simulate_tobe_metrics(
    to_be_bpmn_path: Path,
    baseline: Dict,
    objectives_path: Optional[Path] = None,
    constraints_path: Optional[Path] = None,
    resource_config: Optional[Dict] = None,
) -> Dict:
    """Simulate TO-BE process metrics."""
    graph = BPMNGraph(Path(to_be_bpmn_path))
    ns = {"bpmn": BPMN_NS}
    
    # Try to extract SLA from objectives
    sla_p90_s = None
    if objectives_path and Path(objectives_path).exists():
        try:
            o = json.loads(Path(objectives_path).read_text(encoding="utf-8"))
            sla_p90_s = (
                o.get("sla_seconds_p90") or
                o.get("SLA_P90_seconds") or
                o.get("sla", {}).get("p90_seconds")
            )
        except Exception:
            pass
    
    memo: Dict[str, AggTiming] = {}
    starts = graph.start_nodes()
    if not starts:
        raise ValueError("No startEvent in TO-BE BPMN")
    start_id = starts[0]

    active: Set[str] = set()
    
    def eval_from(nid: str) -> AggTiming:
        if nid in memo:
            return memo[nid]
        if nid in active:  # Cycle guard
            return AggTiming(0, 0, 0)

        active.add(nid)
        try:
            node = graph.nodes[nid]

            # End event
            if node.type == "endEvent":
                res = AggTiming(0, 0, 0)
                memo[nid] = res
                return res

            # Task nodes
            if node.type.endswith("Task") or node.type in ("subProcess", "callActivity"):
                # dazugehöriges BPMN-Element finden
                el = None
                for e in list(graph.process):
                    if e.get("id") == nid:
                        el = e
                        break

                # Task-Timings aus Baseline (inkl. Automation-Faktor)
                t = _task_timing_from_baseline(
                    node.name,
                    baseline,
                    _is_automated(node, el) if el is not None else False
                )

                # Nachfolger sammeln
                succ = [graph.flows[f][1] for f in node.outgoing if f in graph.flows]

                # Rekursiv weiter (mit Guard)
                if not succ:
                    agg_next = AggTiming(0, 0, 0)
                elif len(succ) == 1:
                    agg_next = eval_from(succ[0])
                else:
                    agg_next = _and([eval_from(s) for s in succ])

                res = _seq(AggTiming(t.mean_s, t.p50_s, t.p90_s), agg_next)
                memo[nid] = res
                return res

            # Exclusive gateway (XOR)
            if node.type == "exclusiveGateway":
                succ = [graph.flows[f][1] for f in node.outgoing if f in graph.flows]
                probs = _xor_probs(graph, nid, baseline)
                branches = [(float(probs.get(s, 0.0)), eval_from(s)) for s in succ]

                # keine Wahrscheinlichkeiten vorhanden → gleichmäßig verteilen
                if not any(p > 0 for p, _ in branches) and branches:
                    u = 1.0 / len(branches)
                    branches = [(u, t) for _, t in branches]

                res = _xor(branches)
                memo[nid] = res
                return res

            # Parallel gateway (AND)
            if node.type == "parallelGateway":
                succ = [graph.flows[f][1] for f in node.outgoing if f in graph.flows]
                if len(succ) > 1:
                    res = _and([eval_from(s) for s in succ])
                elif len(succ) == 1:
                    res = eval_from(succ[0])
                else:
                    res = AggTiming(0, 0, 0)
                memo[nid] = res
                return res

            # Andere Events
            if node.type.endswith("Event"):
                succ = [graph.flows[f][1] for f in node.outgoing if f in graph.flows]
                if not succ:
                    res = AggTiming(0, 0, 0)
                elif len(succ) == 1:
                    res = eval_from(succ[0])
                else:
                    res = _and([eval_from(s) for s in succ])
                memo[nid] = res
                return res

            # Default: einfach den/ die Nachfolger auswerten
            succ = [graph.flows[f][1] for f in node.outgoing if f in graph.flows]
            if not succ:
                res = AggTiming(0, 0, 0)
            elif len(succ) == 1:
                res = eval_from(succ[0])
            else:
                res = _and([eval_from(s) for s in succ])

            memo[nid] = res
            return res

        finally:
            # immer vom Stack entfernen – auch bei Exceptions/Returns
            active.remove(nid)
    
    # Calculate total
    total = eval_from(start_id)
    
    # Cost calculation
    cost_per_case = None
    if resource_config and isinstance(resource_config, dict):
        rates = resource_config.get("rates", {})
        pa = baseline.get("per_activity", {})
        cost = 0.0
        
        for n in graph.nodes.values():
            if n.type.endswith("Task"):
                s = pa.get(n.name)
                if s:
                    rate = float(rates.get(n.name, 0.0))
                    cost += float(s.get("service_mean_s", 0.0)) * rate
        
        cost_per_case = cost
    
    # Build output
    out = {
        "cycle_mean_s": total.mean_s,
        "cycle_p50_s": total.p50_s,
        "cycle_p90_s": total.p90_s,
        "cost_per_case": cost_per_case
    }
    
    # SLA check
    if sla_p90_s is not None:
        out["sla_p90_seconds"] = float(sla_p90_s)
        out["on_time_p90"] = 1.0 if total.p90_s <= float(sla_p90_s) else 0.0
    
    # Per-node metrics
    per_node = {}
    for nid, n in graph.nodes.items():
        if nid in memo:
            t = memo[nid]
            per_node[nid] = {
                "type": n.type,
                "name": n.name,
                "from_here_mean_s": t.mean_s,
                "from_here_p50_s": t.p50_s,
                "from_here_p90_s": t.p90_s
            }
    
    out["per_node"] = per_node
    
    return out