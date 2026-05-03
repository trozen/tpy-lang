# Re-exporting a Final[T] constant through a flat `# tpy: native_module`
# (not __init__.py). Exercises the can_reexport extension to native_module
# facades plus the reexport-chain chase in codegen.
from facade import VERSION, LIMIT
from tpy import Int32


def main() -> None:
    print(VERSION)
    print(LIMIT)
    print(Int32(LIMIT) + Int32(1))


main()
