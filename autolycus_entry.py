#!/usr/bin/env python3
"""
Autolycus entry point. Sets AUTOLYCUS_HOME before delegating to hermes_cli.main.
"""
import os
import sys
from pathlib import Path

# Default AUTOLYCUS_HOME = ~/.autolycus (isolated from Hermes ~/.hermes/)
auto_home = os.environ.get("AUTOLYCUS_HOME")
if auto_home:
    ah = Path(auto_home)
else:
    ah = Path.home() / ".autolycus"
    os.environ["AUTOLYCUS_HOME"] = str(ah)

# Ensure the directory exists
ah.mkdir(parents=True, exist_ok=True)

# Set HERMES_HOME to AUTOLYCUS_HOME so all internal imports use it
os.environ["HERMES_HOME"] = str(ah)

# Use config.yaml from autolycus home if exists, otherwise default profile
config_dir = ah
config_file = config_dir / "config.yaml"
env_file = config_dir / ".env"

# Delegate to hermes_cli.main
from hermes_cli.main import main
sys.exit(main())
