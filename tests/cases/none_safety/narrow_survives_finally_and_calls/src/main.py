# Soundness fixes must not over-kill: narrowing survives loops that don't
# touch the variable, plain calls (no nonlocal-writing closure in scope),
# and code after a try/finally on the normal path.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def log() -> None:
    print("log")


def main():
    p: Point | None = Point(1)
    if p is None:
        return
    i = 0
    while i < 2:
        print(p.x)  # tpyc: ok
        i += 1
    log()
    print(p.x)  # tpyc: ok
    try:
        print("try")
    finally:
        print("fin")
    print(p.x)  # tpyc: ok


main()
