"""Persistence layer for weekly runs and per-source snapshots.

Manages layout:
  data/
    runs/<iso_week>/
      reviews_clean.jsonl
      themes.json
      pulse.json
      pulse.md
      eval_report.json
      run_manifest.json
    snapshots/<source>/<iso_week>.jsonl

Guarantees:
  - Clean reviews only; raw reviews are never written to disk.
  - Idempotent writes.
"""

import json
from pathlib import Path
from typing import Any

from pulse.models import CleanReview, PublishState, Pulse, RunManifest, Source, Theme


class RunStore:
    """Read/write helpers for run artifacts and snapshots."""

    def __init__(self, base_dir: Path | str = "data"):
        self.base_dir = Path(base_dir)

    def run_dir(self, week: str) -> Path:
        """Returns and creates the directory for a given weekly run."""
        d = self.base_dir / "runs" / week
        d.mkdir(parents=True, exist_ok=True)
        return d

    def snapshot_dir(self, source: Source | str) -> Path:
        """Returns and creates the directory for a given source's weekly snapshots."""
        d = self.base_dir / "snapshots" / source
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- Clean reviews ---
    def save_clean_reviews(self, week: str, reviews: list[CleanReview]) -> Path:
        target = self.run_dir(week) / "reviews_clean.jsonl"
        with target.open("w", encoding="utf-8") as f:
            for r in reviews:
                f.write(r.model_dump_json() + "\n")
        return target

    def load_clean_reviews(self, week: str) -> list[CleanReview]:
        target = self.run_dir(week) / "reviews_clean.jsonl"
        if not target.exists():
            return []
        reviews: list[CleanReview] = []
        with target.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    reviews.append(CleanReview.model_validate_json(line))
        return reviews

    # --- Weekly snapshots ---
    def save_snapshot(self, source: Source, week: str, reviews: list[CleanReview]) -> Path:
        target = self.snapshot_dir(source) / f"{week}.jsonl"
        with target.open("w", encoding="utf-8") as f:
            for r in reviews:
                f.write(r.model_dump_json() + "\n")
        return target

    def load_snapshot(self, source: Source, week: str) -> list[CleanReview]:
        target = self.snapshot_dir(source) / f"{week}.jsonl"
        if not target.exists():
            return []
        reviews: list[CleanReview] = []
        with target.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    reviews.append(CleanReview.model_validate_json(line))
        return reviews

    def load_snapshots_for_weeks(self, source: Source, weeks: list[str]) -> list[CleanReview]:
        all_reviews: list[CleanReview] = []
        for w in weeks:
            all_reviews.extend(self.load_snapshot(source, w))
        return all_reviews

    # --- Themes ---
    def save_themes(self, week: str, themes: list[Theme]) -> Path:
        target = self.run_dir(week) / "themes.json"
        with target.open("w", encoding="utf-8") as f:
            json.dump([t.model_dump() for t in themes], f, indent=2)
        return target

    def load_themes(self, week: str) -> list[Theme] | None:
        target = self.run_dir(week) / "themes.json"
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as f:
            data = json.load(f)
        return [Theme.model_validate(t) for t in data]

    # --- Assignments ---
    def save_assignments(self, week: str, assignments: list[Any]) -> Path:
        target = self.run_dir(week) / "assignments.jsonl"
        with target.open("w", encoding="utf-8") as f:
            for a in assignments:
                if hasattr(a, "model_dump_json"):
                    f.write(a.model_dump_json() + "\n")
                else:
                    f.write(json.dumps(a) + "\n")
        return target

    def load_assignments(self, week: str) -> list[dict[str, Any]]:
        target = self.run_dir(week) / "assignments.jsonl"
        if not target.exists():
            return []
        items: list[dict[str, Any]] = []
        with target.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items

    # --- Pulse ---
    def save_pulse(self, week: str, pulse: Pulse) -> tuple[Path, Path]:
        d = self.run_dir(week)
        json_path = d / "pulse.json"
        md_path = d / "pulse.md"

        with json_path.open("w", encoding="utf-8") as f:
            f.write(pulse.model_dump_json(indent=2))

        with md_path.open("w", encoding="utf-8") as f:
            f.write(pulse.markdown)

        return json_path, md_path

    def load_pulse(self, week: str) -> Pulse | None:
        target = self.run_dir(week) / "pulse.json"
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as f:
            return Pulse.model_validate_json(f.read())

    # --- Manifest ---
    def save_manifest(self, manifest: RunManifest) -> Path:
        target = self.run_dir(manifest.week) / "run_manifest.json"
        with target.open("w", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))
        return target

    def load_manifest(self, week: str) -> RunManifest | None:
        target = self.run_dir(week) / "run_manifest.json"
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as f:
            return RunManifest.model_validate_json(f.read())

    # --- Publish state (per week) and shared Doc reference ---
    def save_publish_state(self, state: PublishState) -> Path:
        target = self.run_dir(state.week) / "publish_state.json"
        with target.open("w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=2))
        return target

    def load_publish_state(self, week: str) -> PublishState | None:
        target = self.run_dir(week) / "publish_state.json"
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as f:
            return PublishState.model_validate_json(f.read())

    def _doc_ref_path(self) -> Path:
        d = self.base_dir / "state"
        d.mkdir(parents=True, exist_ok=True)
        return d / "publish_state.json"

    def save_doc_ref(self, ref: dict[str, Any]) -> Path:
        target = self._doc_ref_path()
        with target.open("w", encoding="utf-8") as f:
            json.dump(ref, f, indent=2)
        return target

    def load_doc_ref(self) -> dict[str, Any] | None:
        target = self._doc_ref_path()
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]

    # --- Eval report ---
    def save_eval_report(self, week: str, report: dict[str, Any]) -> Path:
        target = self.run_dir(week) / "eval_report.json"
        with target.open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        return target

    def load_eval_report(self, week: str) -> dict[str, Any] | None:
        target = self.run_dir(week) / "eval_report.json"
        if not target.exists():
            return None
        with target.open(encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
