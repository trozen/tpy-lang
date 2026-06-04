# A function macro mints the bool type via ctx.resolve_type("bool") -- not
# borrowed from the return type (which is Int32) -- to retype a string-literal
# bool local. Proves resolve_type supplies a usable type by name.
from resolvemod import resolve_bool
from tpy import Int32


@resolve_bool
def run() -> Int32:
    flag = "true"
    return 1 if flag else 0


def main() -> None:
    print(run())


main()
