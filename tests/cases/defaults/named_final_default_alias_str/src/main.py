# Aliased import (`from X import Y as Z`) and Final[str] as default values:
# the local alias must look up the source binding, and codegen must emit the
# qualified original name (not the alias) since the alias is not a C++ symbol.
from tpy import uint32, int32
from consts import CASELESS as DEFAULT_FLAGS
from consts import DEFAULT_GREETING


def use_alias(flags: uint32 = DEFAULT_FLAGS) -> int32:
    return int32(flags)


def greet(prefix: str = DEFAULT_GREETING) -> str:
    return prefix


def main() -> None:
    print(use_alias())
    print(use_alias(uint32(0)))
    print(greet())
    print(greet("hi"))


main()
