# visualization_agent.py
# -*- coding: utf-8 -*-
"""
VisualizationAgent (ohne Dummy-Daten)

- Nutzt ausschließlich echte Vergleichsdaten (before/after) aus 'comparison'.
- Erzeugt Charts nur, wenn die dafür nötigen Daten vollständig vorhanden sind.
- Keine Zufallswerte, keine hartkodierten Beispiel-Arrays.
- Fail-Closed: Wenn Daten fehlen/leer sind → kein Chart.

Erwartete comparison-Struktur (alles optional; Charts erscheinen nur, wenn vorhanden):
comparison = {
  "global_before": {
      "throughput_time_mean_s": float,
      "service_mean_s": float,
      "waiting_mean_s": float,
      "wip_mean": float,
      "cost_mean": float
  },
  "global_after": { ... gleiche Keys ... },

  "per_activity_before": {
      "<act_id_or_name>": {
          "service_mean_s": float,
          "waiting_mean_s": float,
          "rework_rate": float,
          "automation_level": float,
          "cost_mean": float
      },
      ...
  },
  "per_activity_after": { ... gleiche Struktur ... },

  "path_share_before": { "<path_label>": float, ... },  # z.B. absolute Häufigkeit oder Anteil
  "path_share_after":  { "<path_label>": float, ... },

  "bottlenecks_before": [{"activity": str, "waiting_mean_s": float, "service_mean_s": float}, ...],
  "bottlenecks_after":  [{"activity": str, "waiting_mean_s": float, "service_mean_s": float}, ...],

  "measures": [  # optional – echte Maßnahmen mit Zeiten
     {"label": str, "start_week": int, "end_week": int},
     ...
  ],
  "scenarios": {  # optional – echte Szenarien mit Dauer
     "S1": {"implementation_weeks": int, "measures": [ ... wie oben ... ]},
     ...
  }
}

Alle Funktionen geben Pfade zu erzeugten PNGs/CSV zurück oder None.
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import logging
import json

import matplotlib
matplotlib.use("Agg")  # Headless
import matplotlib.pyplot as plt
import numpy as np


logger = logging.getLogger(__name__)


class VisualizationAgent:
    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)

    # -------- Public API --------

    def run(self, sid: str, comparison: Dict[str, Any], baseline: Optional[Dict[str, Any]] = None,
            prioritization: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[Path]]:
        """
        Erstellt verfügbare Visualisierungen aus realen Daten.
        Gibt ein Dict mit Artefaktpfaden zurück; fehlende Artefakte → None.
        """
        self._ensure_dir(self.out_dir)
        artifacts: Dict[str, Optional[Path]] = {}

        # 1) KPI-Vergleich (Global)
        artifacts["kpi_global"] = self._chart_kpi_global_bars(comparison, self.out_dir, sid)

        # 2) Aktivitäts-Heatmap (echte Improvements)
        artifacts["activity_improvement_heatmap"] = self._chart_activity_improvement_heatmap(
            comparison, self.out_dir, sid
        )

        # 3) Pfadanteile: Vergleich (Stacked Bars). Sankey nur, wenn korrekt bilanzierbar
        # (Matplotlib-Sankey ist heikel; wir erzeugen primär eine valide Balkenansicht.)
        artifacts["path_share_bars"] = self._chart_path_share_bars(comparison, self.out_dir, sid)
        # Optional: Sankey – nur, wenn Summe As-Is == Summe To-Be und numerisch stabil
        artifacts["path_share_sankey"] = self._chart_path_share_sankey_safe(comparison, self.out_dir, sid)

        # 4) Bottleneck-Vergleich (Top-N nach Waiting Time)
        artifacts["bottlenecks"] = self._chart_bottlenecks_before_after(comparison, self.out_dir, sid)

        # 5) Implementierungs-Timeline (nur echte Maßnahmen/Szenarien)
        artifacts["timeline"] = self._chart_implementation_timeline(prioritization, self.out_dir, sid)

        # 6) Export der Vergleichsdaten (JSON) – zur Nachvollziehbarkeit
        artifacts["comparison_export"] = self._export_comparison_json(comparison, self.out_dir, sid)

        return artifacts

    # -------- Charts --------

    def _chart_kpi_global_bars(self, comparison: Dict[str, Any], out_dir: Path, sid: str) -> Optional[Path]:
        """
        Balkendiagramm: globale KPIs (Before vs After). Nur mit echten Werten.
        """
        if not isinstance(comparison, dict):
            return None
        before = comparison.get("global_before") or {}
        after = comparison.get("global_after") or {}
        if not before or not after or not isinstance(before, dict) or not isinstance(after, dict):
            logger.info("Global KPI: missing 'global_before' or 'global_after' → skip")
            return None

        # Wähle gängige Metriken; nur vorhandene darstellen
        metric_candidates = [
            # Cycle/Throughput (beide Varianten akzeptieren)
            (["cycle_mean_s", "throughput_time_mean_s"], "Cycle Time (mean)", False),
            (["cycle_p50_s",  "throughput_time_p50_s"],  "Cycle Time (P50)",  False),
            (["cycle_p90_s",  "throughput_time_p90_s"],  "Cycle Time (P90)",  False),
            # Optional weitere, falls vorhanden
            (["service_mean_s"], "Service Time (mean)", False),
            (["waiting_mean_s"], "Waiting Time (mean)", False),
            (["wip_mean"],       "WIP (avg)",           False),
            (["cost_mean"],      "Cost (avg)",          False),
        ]

        labels, vals_b, vals_a, better_high = [], [], [], []
        for keys, label, high_is_better in metric_candidates:
            # nimm den ersten Key, der in BOTH before/after numerisch belegt ist
            key = next(
                (k for k in keys
                if isinstance(before.get(k), (int, float))
                and isinstance(after.get(k),  (int, float))),
                None
            )
            if key:
                labels.append(label)
                vals_b.append(float(before[key]))
                vals_a.append(float(after[key]))
                better_high.append(high_is_better)

        if not labels:
            logger.info("Global KPI: no numeric pairs → skip")
            return None

        idx = np.arange(len(labels))
        width = 0.38
        fig, ax = plt.subplots(figsize=(10, max(4, len(labels)*0.45)))
        ax.bar(idx - width/2, vals_b, width, label="As-Is")
        ax.bar(idx + width/2, vals_a, width, label="To-Be")

        ax.set_xticks(idx)
        ax.set_xticklabels(labels, rotation=20, ha="right")
        ax.set_title("Global KPIs — As-Is vs To-Be (real data)")
        ax.legend(loc="best")
        ax.grid(True, axis="y", alpha=0.3)

        for i, (b, a) in enumerate(zip(vals_b, vals_a)):
            ax.text(i - width/2, b, f"{b:.2f}", ha="center", va="bottom", fontsize=8)
            ax.text(i + width/2, a, f"{a:.2f}", ha="center", va="bottom", fontsize=8)

        plt.tight_layout()
        path = out_dir / f"kpi_global_{sid}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path



    def _chart_activity_improvement_heatmap(self, comparison: Dict[str, Any], out_dir: Path, sid: str) -> Optional[Path]:
        """
        Heatmap echter Aktivitäts-Verbesserungen (Before vs After).
        Keine Randoms. Nur rendern, wenn per_activity_* vorhanden und numerisch.
        """
        per_b = (comparison or {}).get("per_activity_before") or {}
        per_a = (comparison or {}).get("per_activity_after") or {}
        if not per_b or not per_a:
            logger.info("Activity heatmap: missing per_activity_* → skip")
            return None

        # (key, label, higher_is_better)
        metrics = [
            ("service_mean_s",   "Service Time",     False),
            ("waiting_mean_s",   "Waiting Time",     False),
            ("rework_rate",      "Rework Rate",      False),
            ("automation_level", "Automation Level", True),
            ("cost_mean",        "Cost",             False),
        ]

        acts, rows = [], []
        for act, bstats in per_b.items():
            astats = per_a.get(act)
            if not isinstance(bstats, dict) or not isinstance(astats, dict):
                continue
            row, valid = [], False
            for key, _label, high_is_better in metrics:
                b = bstats.get(key)
                a = astats.get(key)
                if isinstance(b, (int, float)) and isinstance(a, (int, float)) and b != 0:
                    val = ((a - b) / b * 100.0) if high_is_better else ((b - a) / b * 100.0)
                    row.append(val)
                    valid = True
                else:
                    row.append(0.0)
            if valid:
                acts.append(str(act)[:30])
                rows.append(row)

        if not rows:
            logger.info("Activity heatmap: no valid numeric rows → skip")
            return None

        data = np.array(rows, dtype=float)
        fig, ax = plt.subplots(figsize=(10, max(6, len(acts) * 0.4)))
        im = ax.imshow(data, aspect="auto")

        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label("Improvement (%)", rotation=270, labelpad=20)

        ax.set_yticks(np.arange(len(acts)))
        ax.set_yticklabels(acts)
        ax.set_xticks(np.arange(len(metrics)))
        ax.set_xticklabels([m[1] for m in metrics], rotation=45, ha="right")
        ax.set_title("Activity-by-Activity Improvements (real data only)")

        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                ax.text(j, i, f"{data[i, j]:.1f}", ha="center", va="center", fontsize=8)

        ax.grid(False)
        plt.tight_layout()
        path = out_dir / f"activity_improvement_heatmap_{sid}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def _chart_path_share_bars(self, comparison: Dict[str, Any], out_dir: Path, sid: str) -> Optional[Path]:
        """
        Vergleich Pfadanteile (Before vs After) als gestapelte Balken — nur aus realen path_share_*.
        """
        before = (comparison or {}).get("path_share_before") or {}
        after = (comparison or {}).get("path_share_after") or {}
        if not before or not after or not isinstance(before, dict) or not isinstance(after, dict):
            logger.info("Path shares (bars): missing path_share_* → skip")
            return None

        paths = sorted(set(before.keys()) | set(after.keys()))
        if not paths:
            return None

        b_vals = np.array([float(before.get(p, 0.0)) for p in paths], dtype=float)
        a_vals = np.array([float(after.get(p, 0.0)) for p in paths], dtype=float)

        def _normalize(x: np.ndarray) -> np.ndarray:
            s = float(np.sum(x))
            return x / s if s > 0 else x

        b = _normalize(b_vals)
        a = _normalize(a_vals)

        fig, ax = plt.subplots(figsize=(10, 5))

        # Gestapelte Balken: zwei Balken (As-Is, To-Be), gestapelt mit denselben Pfad-Reihenfolgen
        idx = np.arange(2)
        bottom_b = 0.0
        bottom_a = 0.0
        for i, p in enumerate(paths):
            ax.bar(0, b[i], bottom=bottom_b)
            ax.bar(1, a[i], bottom=bottom_a)
            bottom_b += b[i]
            bottom_a += a[i]

        ax.set_xticks(idx)
        ax.set_xticklabels(["As-Is", "To-Be"])
        ax.set_ylim(0, 1.0)
        ax.set_title("Path Share Comparison (real data)")
        ax.grid(True, axis="y", alpha=0.3)

        # Legende als Liste (einfach): Pfadlabels
        # (Matplotlib ohne Farben → Legende generisch)
        # Wir schreiben stattdessen pro Pfad die Anteile oben rechts als Text:
        text_lines = []
        for i, p in enumerate(paths):
            text_lines.append(f"{p[:30]} — As-Is {b[i]*100:.1f}%, To-Be {a[i]*100:.1f}%")
        ax.text(1.05, 0.5, "\n".join(text_lines), transform=ax.transAxes, va="center")

        plt.tight_layout()
        path = out_dir / f"path_share_bars_{sid}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def _chart_path_share_sankey_safe(self, comparison: Dict[str, Any], out_dir: Path, sid: str) -> Optional[Path]:
        """
        Optionaler Sankey, nur wenn numerisch sauber möglich (Summe Before == Summe After > 0).
        Wenn nicht möglich → None (kein Chart).
        """
        before = (comparison or {}).get("path_share_before") or {}
        after = (comparison or {}).get("path_share_after") or {}
        if not before or not after:
            return None

        paths = sorted(set(before.keys()) | set(after.keys()))
        if not paths:
            return None

        b_vals = np.array([float(before.get(p, 0.0)) for p in paths], dtype=float)
        a_vals = np.array([float(after.get(p, 0.0)) for p in paths], dtype=float)

        sb = float(np.sum(b_vals))
        sa = float(np.sum(a_vals))
        if sb <= 0 or sa <= 0:
            return None

        # Für Matplotlib.Sankey müssen Flüsse je Block in Summe 0 ergeben.
        # Wir bauen daher zwei Blöcke: Quelle (gesamt) → Ziele (Anteile).
        # Flüsse: [-1.0] (Quelle) und dann die Zielanteile [+a_norm...], sodass Summe 0 ist.
        a_norm = (a_vals / sa).tolist()
        flows = [-1.0] + a_norm  # Summe = 0, wenn sum(a_norm) == 1.0
        if not np.isclose(sum(a_norm), 1.0, atol=1e-6):
            # Numerische Sicherheit: notfalls abbrechen
            return None

        try:
            fig = plt.figure(figsize=(12, 5))
            ax = fig.add_subplot(1, 1, 1, xticks=[], yticks=[])
            from matplotlib.sankey import Sankey
            sankey = Sankey(ax=ax, scale=1.0, format="%.1f", shoulder=0.1, margin=0.5)
            sankey.add(
                flows=flows,
                orientations=[0] * len(flows),
                labels=["As-Is total"] + [p[:20] for p in paths],
                trunklength=1.0
            )
            sankey.finish()
            ax.set_title("Sankey — To-Be path distribution from As-Is total (real data)")
            plt.tight_layout()
            path = out_dir / f"path_share_sankey_{sid}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            return path
        except Exception as e:
            logger.warning(f"Sankey skipped (reason: {e})")
            return None

    def _chart_bottlenecks_before_after(self, comparison: Dict[str, Any], out_dir: Path, sid: str) -> Optional[Path]:
        """
        Bottleneck-Vergleich (Top-N nach Waiting Time). Nur mit echten Listen.
        """
        bn_b = (comparison or {}).get("bottlenecks_before") or []
        bn_a = (comparison or {}).get("bottlenecks_after") or []
        if not isinstance(bn_b, list) or not isinstance(bn_a, list) or (not bn_b and not bn_a):
            logger.info("Bottlenecks: missing lists → skip")
            return None

        # Map nach Activity, pick Waiting Time (wenn vorhanden)
        def _to_map(lst: List[Dict[str, Any]]) -> Dict[str, float]:
            m = {}
            for x in lst:
                act = str(x.get("activity", "")).strip()
                w = x.get("waiting_mean_s")
                if act and isinstance(w, (int, float)):
                    m[act] = float(w)
            return m

        mb = _to_map(bn_b)
        ma = _to_map(bn_a)
        if not mb and not ma:
            return None

        # gemeinsame Menge; falls unterschiedlich → unify
        acts = sorted(set(mb.keys()) | set(ma.keys()))
        if not acts:
            return None

        # Top N (z. B. 10) nach Before-Waiting
        N = min(10, len(acts))
        acts_sorted = sorted(acts, key=lambda k: mb.get(k, 0.0), reverse=True)[:N]

        vals_b = [mb.get(a, 0.0) for a in acts_sorted]
        vals_a = [ma.get(a, 0.0) for a in acts_sorted]

        idx = np.arange(len(acts_sorted))
        width = 0.38

        fig, ax = plt.subplots(figsize=(12, max(5, len(acts_sorted) * 0.45)))
        ax.bar(idx - width / 2, vals_b, width, label="As-Is")
        ax.bar(idx + width / 2, vals_a, width, label="To-Be")
        ax.set_xticks(idx)
        ax.set_xticklabels([a[:30] for a in acts_sorted], rotation=30, ha="right")
        ax.set_ylabel("Waiting Time (s)")
        ax.set_title("Bottlenecks — Waiting Time (Top 10, real data)")
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend(loc="best")

        for i, (b, a) in enumerate(zip(vals_b, vals_a)):
            ax.text(i - width / 2, b, f"{b:.1f}", ha="center", va="bottom", fontsize=8)
            ax.text(i + width / 2, a, f"{a:.1f}", ha="center", va="bottom", fontsize=8)

        plt.tight_layout()
        path = out_dir / f"bottlenecks_{sid}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def _chart_implementation_timeline(self, data: Optional[Dict[str, Any]], out_dir: Path, sid: str) -> Optional[Path]:
        """
        Timeline echter Maßnahmen/Szenarien. Keine Platzhalter.
        Erwartet:
          - data["measures"] mit start_week/end_week
            ODER
          - data["scenarios"][name]["implementation_weeks"]
        Ohne diese Felder → kein Chart.
        """
        if not isinstance(data, dict):
            return None

        measures = data.get("measures") if "measures" in data else None
        scenarios = data.get("scenarios") if "scenarios" in data else None

        bars: List[Tuple[str, int, int]] = []  # (label, start, end)
        if isinstance(measures, list):
            for m in measures:
                label = str(m.get("label", "")).strip()
                sw = m.get("start_week")
                ew = m.get("end_week")
                if label and isinstance(sw, int) and isinstance(ew, int) and ew >= sw:
                    bars.append((label, sw, ew))

        if not bars and isinstance(scenarios, dict):
            # Szenarien mit Dauer; wir visualisieren sie nacheinander (S1, S2, …)
            week_cursor = 0
            for name, info in scenarios.items():
                w = info.get("implementation_weeks")
                if isinstance(w, int) and w > 0:
                    bars.append((str(name), week_cursor, week_cursor + w))
                    week_cursor += w

        if not bars:
            logger.info("Timeline: no real measures/scenarios → skip")
            return None

        labels = [b[0][:40] for b in bars]
        starts = np.array([b[1] for b in bars], dtype=int)
        ends = np.array([b[2] for b in bars], dtype=int)
        durations = ends - starts

        fig, ax = plt.subplots(figsize=(12, max(4, len(bars) * 0.4)))
        y = np.arange(len(bars))
        for i in range(len(bars)):
            ax.barh(y[i], durations[i], left=starts[i])
            ax.text(starts[i] + durations[i] / 2.0, y[i], f"{durations[i]}w", ha="center", va="center", fontsize=8)

        ax.set_yticks(y)
        ax.set_yticklabels(labels)
        ax.set_xlabel("Weeks")
        ax.set_title("Implementation Timeline (real data)")
        ax.grid(True, axis="x", alpha=0.3)

        plt.tight_layout()
        path = out_dir / f"implementation_timeline_{sid}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    # -------- Exporte / Utils --------

    def _export_comparison_json(self, comparison: Dict[str, Any], out_dir: Path, sid: str) -> Optional[Path]:
        try:
            path = out_dir / f"comparison_{sid}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(comparison or {}, f, ensure_ascii=False, indent=2)
            return path
        except Exception as e:
            logger.warning(f"comparison export failed: {e}")
            return None

    @staticmethod
    def _ensure_dir(d: Path) -> None:
        d.mkdir(parents=True, exist_ok=True)
