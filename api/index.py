"""Vercel serverless entrypoint: the FastAPI app from pulse.api, read-only over bundled data."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("PULSE_READ_ONLY", "1")
os.environ.setdefault("PULSE_DATA_DIR", str(ROOT / "deploy_data"))
os.environ.setdefault("PULSE_CONFIG_PATH", str(ROOT / "config" / "config.yaml"))
os.environ.setdefault("PULSE_MCP_CONFIG_PATH", str(ROOT / "config" / "mcp_servers.yaml"))

from pulse.api import app  # noqa: E402,F401
