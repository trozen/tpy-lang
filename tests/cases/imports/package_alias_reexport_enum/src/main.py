# Aliased enum re-exported through pkg/__init__.py (`from .colors import Color as C`).
# Mirror of package_alias_reexport_record for the enum path in
# compiler.py::_exports_to_module_info re-export dedupe.
from pkg import C

def main() -> None:
    c = C.Green
    print(c.name)

main()
