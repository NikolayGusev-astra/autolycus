import os, sys
from pathlib import Path

# Set up AUTOLYCUS_HOME before importing anything
ah = os.environ.get("AUTOLYCUS_HOME") or str(Path.home() / ".autolycus")
os.environ.setdefault("AUTOLYCUS_HOME", ah)
os.environ["HERMES_HOME"] = ah  # override so all internal code uses fork home

ah_path = Path(ah)
ah_path.mkdir(parents=True, exist_ok=True)

from hermes_cli.main import main
sys.exit(main())

