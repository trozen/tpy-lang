# Star import of a type alias from a user module. Resolves at parse
# time via the parser name_index populated by the compile-time
# star-import expansion (Compiler._expand_star_imports_for_module);
# the source module's sema has finalized by the time the consumer's
# resolve_refs runs, so the alias is registered and the annotation
# `x: MyInt` resolves cleanly. The alias is used only in annotation
# position; PEP 695 `type X = T` is not callable under CPython.
from lib import *


def main() -> None:
    x: MyInt = 42
    print(x)


main()
