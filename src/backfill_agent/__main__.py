"""Entry point so `python -m src.backfill_agent` works."""
import sys
from src.backfill_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
