"""Food-delivery application built on Objective A's job and event formats."""
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
OBJ_A = APP.parent / "obj-a"
sys.path.insert(0, str(OBJ_A))
