# Aliased record re-exported through pkg/__init__.py (`from .sub import Point as P`).
# Exercises the aliased-import dedupe in compiler.py::_exports_to_module_info:
# registry.records has both `P` (alias) and `Point` (canonical); only `P` must
# re-export, matching CPython's `from pkg import P` semantics.
from pkg import P

def main() -> None:
    pt = P(3, 4)
    print(pt)

main()
