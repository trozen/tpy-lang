# Call macro from a package: macro_deps with dotted module, ast.name("a.b").
from tpy import int32
from mypkg.macros import make_tag


def main() -> None:
    s = make_tag("x", 42)
    print(s)

main()
