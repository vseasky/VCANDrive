#!/usr/bin/env python3
"""Use this same entrypoint on Windows, Linux and macOS; no dependencies for help/setup."""
from tools.launcher import main

if __name__ == '__main__':
    raise SystemExit(main())
