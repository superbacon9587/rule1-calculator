#!/usr/bin/env python3
"""
Convenience wrapper so you can run:

    python analyze.py AAPL
    python analyze.py AAPL MSFT COST

instead of `python -m rule1.cli ...`. Same thing either way.
"""

from rule1.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
