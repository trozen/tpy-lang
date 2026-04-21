"""Allow running as: python -m tpyc"""
from .cli import main_tpyc
import sys

if __name__ == "__main__":
    sys.exit(main_tpyc())
