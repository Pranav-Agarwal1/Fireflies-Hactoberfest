#!/usr/bin/env python3
"""Guided setup entry point. No package installation is required."""
import sys

if sys.version_info < (3, 9):
    print('Python 3.9+ is required. On macOS, launch setup.command.', file=sys.stderr)
    raise SystemExit(2)

from mainline_agent.setup_wizard import main

if __name__ == '__main__':
    raise SystemExit(main())
