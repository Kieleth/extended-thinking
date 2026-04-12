"""Allow `python -m extended_thinking` to work as a CLI entry point."""
import sys

from extended_thinking.cli import main

sys.exit(main())
