# FixStr relocation: a forced (non-elided) move runs FixStr.__move__ ->
# relocate_from; the live chars survive and the relocated handle is usable.
from tpy import Char, Own
from tplib import FixStr


def make() -> Own[FixStr[16]]:
    s = FixStr[16]()
    a: Char = "a"
    b: Char = "b"
    s.append(a)
    s.append(b)
    return s


def main() -> None:
    src = make()
    moved = src                  # last-use rebind -> forced move (relocate_from)
    print(len(moved))            # 2
    print(moved[0], moved[1])    # a b
    c: Char = "c"
    moved.append(c)              # mutate the relocated handle
    print(len(moved), moved[2])  # 3 c


main()
