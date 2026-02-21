# Unknown type names are rejected during semantic analysis.
from tpy import Int32

class Foo:
    x: Int32

def main() -> None:
    bar: UnknownType = Foo(1)  # tpyc: error(/Unknown type: UnknownType/)

main()
