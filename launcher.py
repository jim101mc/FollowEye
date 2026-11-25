"""Entry script for PyInstaller (it needs a script outside the package)."""
import sys

from followeye.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
