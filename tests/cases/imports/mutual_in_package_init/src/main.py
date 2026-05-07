# A package __init__.py inside a cycle: previously rejected by the
# cycle-facade gate; now accepted thanks to Phase 5's universal
# re-export and cycle-aware `using` suppression. CPython hits a real
# circular-import error on this exact pkg/__init__.py + helper.py
# shape, so the cpy phase is skipped via no_cpython.txt.
from pkg import Boosted
from tpy import Int32

def main() -> Int32:
    return Boosted(3).boost()

main()
