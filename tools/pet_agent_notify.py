#!/usr/bin/env python3
"""Optional command-hook entrypoint; does not install or start anything."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from regular.pet_notifications import main

if __name__ == "__main__":
    raise SystemExit(main())
