#!/usr/bin/env python3
"""Set up local AI and verify readiness. Run with Python 3.9 or newer."""
import sys

if sys.version_info < (3, 9):
    print('Python 3.9+ is required. Install Python for this operating system first.', file=sys.stderr)
    raise SystemExit(2)

from mainline_agent.bootstrap import main

if __name__ == '__main__':
    raise SystemExit(main())
