# Star import from a non-existent module surfaces at compile time
# (previously at parse time -- substep 3 of the star-import filter
# migration moved expansion to the compile step, so the parser no
# longer needs to resolve the source. The "Module not found" check
# fires from `_process_user_import` instead).
from nonexistent_module import *  # tpyc: error(/Module 'nonexistent_module' not found/)


def main() -> None:
    pass


main()
