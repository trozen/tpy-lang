# `field=None` on a union subject: the None check must run, not match every
# Wrapper regardless of `child`.
class Wrapper:
    child: "str | None"
    def __init__(self, child: "str | None") -> None:
        self.child = child

class Other:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

def describe(w: Wrapper | Other) -> str:
    match w:
        case Wrapper(child=None):  # tpyc: ok
            return "empty wrapper"
        case Wrapper():
            return "full wrapper"
        case _:
            return "other"

def main() -> None:
    print(describe(Wrapper(None)))
    print(describe(Wrapper("hi")))
    print(describe(Other(1)))

main()
