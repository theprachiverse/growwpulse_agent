"""Copy the browsable slice of data/ into deploy_data/ for the read-only Vercel deployment.

Only PII-scrubbed run artifacts are bundled. Publish state (Doc/email IDs), snapshots of raw
reviews, caches, logs and UI settings stay out. Re-run after each pipeline run, then commit
deploy_data/ and redeploy.
"""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "runs"
DST = ROOT / "deploy_data" / "runs"
SKIP = {"publish_state.json"}


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"No runs found at {SRC}; run the pipeline first.")
    if DST.parent.exists():
        shutil.rmtree(DST.parent)
    n = 0
    for f in SRC.rglob("*"):
        if f.is_file() and f.name not in SKIP:
            out = DST / f.relative_to(SRC)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            n += 1
    print(f"Copied {n} files to {DST.parent}")


if __name__ == "__main__":
    main()
