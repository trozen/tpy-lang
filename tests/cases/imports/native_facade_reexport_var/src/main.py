# Re-exporting a Final[T] constant through a `# tpy: native_module`
# package __init__.py. Codegen has to chase the reexport chain to the
# defining module (the facade has no .hpp) and skip the facade-header
# include (it doesn't exist).
from pkg import VERSION, LIMIT
from tpy import Int32


def main() -> None:
    print(VERSION)
    print(LIMIT)
    print(Int32(LIMIT) + Int32(1))


main()
