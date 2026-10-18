"""Compatibility entry point; all runs use the new production engine."""
from helper_engine.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
