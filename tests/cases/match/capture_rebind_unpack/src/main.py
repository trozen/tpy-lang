# A value-typed match capture rebound via tuple-unpack binds by value (a copy):
# the unpack writes the local, leaving the subject's field untouched -- matching
# CPython, where the capture is a fresh local. Exercises the written_names
# TpyTupleUnpack rebind-detection branch end to end.
class Cat:
    lives: int
    def __init__(self, lives: int) -> None:
        self.lives = lives


def unpack_rebind(a: Cat) -> int:
    match a:
        case Cat(lives=v):
            xs: tuple[int, int] = (5, 6)
            v, w = xs             # tuple-unpack rebinds the capture v
            return v + w          # 11
    return -1


def main():
    c = Cat(9)
    print(unpack_rebind(c), c.lives)   # 11 9 -- field untouched by the rebind


main()
