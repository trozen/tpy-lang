# Re-exporting a Final[T] constant through a flat `"mod": "native"` module
# (not __init__.py). Exercises the can_reexport extension to native-module
# facades plus the reexport-chain chase in codegen.
from facade import VERSION, LIMIT
from tpy import int32


def main() -> None:
    print(VERSION)
    print(LIMIT)
    print(int32(LIMIT) + int32(1))


main()
