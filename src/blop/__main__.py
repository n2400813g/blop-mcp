"""Allow running blop as ``python -m blop``."""
from blop.server import run

raise SystemExit(run())
