# A sibling of an escaping closure that captures a list or record changed
# later keeps a warned snapshot, as a directly escaping def does.
from typing import Callable


class R:
    def __init__(self, v: int) -> None:
        self.v = v


# Section s_list: the list is appended to after the sibling captures it.
def s_list() -> Callable[[], int]:
    xs = [1, 2, 3]

    def h() -> int:  # tpyc: warning(/Escaping closure 'h' copies local 'xs'/)
        return sum(xs)

    def esc() -> int:
        return h()
    xs.append(10)
    return esc


# Section s_record: the record's field is set after the sibling captures it.
def s_record() -> Callable[[], int]:
    r = R(5)

    def h() -> int:  # tpyc: warning(/Escaping closure 'h' copies local 'r'/)
        return r.v

    def esc() -> int:
        return h()
    r.v = 50
    return esc


def main() -> None:
    print("s_list", s_list()())
    print("s_record", s_record()())


main()
