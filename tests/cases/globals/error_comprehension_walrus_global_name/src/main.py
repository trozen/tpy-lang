# Refuses valid Python (BUGS.md#comprehension-walrus-global-name-unlowered):
# a comprehension's walrus in a function binds a local of the function even
# where a module variable has the name, and that local, read after the
# comprehension, does not lower yet.
from tpy import int64

gl = int64(0)


def main() -> None:
    xs = [(gl := int64(3) + i) for i in range(2)]
    print(xs, gl)  # tpyc: error(/not yet supported by C\+\+ code generation/)


main()
