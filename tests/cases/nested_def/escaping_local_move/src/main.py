# An escaping closure's capture when the local is at its LAST use: a value
# local is copied into the lambda and a reference local is moved into it,
# neither warning. The last three sections put a SIBLING `def` beside the
# escaping one that does not read the local -- it does not read it at all, or
# its own parameter takes the name -- so the capture must stay the same. A
# sibling that DOES read the local is the copy+warning path
# (nested_def/escaping_local_copy).
from typing import Callable
from tpy import int32

class Config:
    value: int32
    def __init__(self, v: int32) -> None:
        self.value = v

def make_getter() -> Callable[[], int32]:
    cfg = Config(42)
    def get_value() -> int32:  # tpyc: ok
        return cfg.value
    return get_value

def make_counter() -> Callable[[], int32]:
    start = 9
    # value local: captured BY VALUE, so the sibling def below cannot make it
    # a reference into the frame `make_counter` is about to leave
    def read() -> int32:  # tpyc: ok
        return start
    def unrelated() -> int32:  # tpyc: ok
        return 1
    print("unrelated:", unrelated())
    return read

def make_reader() -> Callable[[], int32]:
    xs = [1, 2, 3]
    # the append keeps `xs` a vector rather than a constant array, so the move
    # into the closure below is a real move of owned storage
    xs.append(4)
    # reference local at its last use: MOVED into the closure, sibling or not
    def total() -> int32:  # tpyc: ok
        return len(xs)
    def other() -> int32:  # tpyc: ok
        return 0
    print("other:", other())
    return total

def make_param_shadow() -> Callable[[], int32]:
    xs = [1, 2]
    xs.append(3)
    def total() -> int32:  # tpyc: ok
        return len(xs)
    # the sibling's PARAMETER takes the local's name, so its body reads the
    # parameter and not the local -- no later use, and the move stands
    def unrelated(xs: list[int32]) -> int32:  # tpyc: ok
        return len(xs)
    print("param_shadow:", unrelated([0]))
    return total

def main() -> None:
    getter = make_getter()
    print(getter())
    # the calls are hoisted out of the prints: the factories write to stdout,
    # and an argument that does interleaves ahead of the earlier arguments
    # (BUGS.md#subexpression-right-to-left-eval)
    counted = make_counter()()
    print("counter:", counted)
    read = make_reader()()
    print("reader:", read)
    shadowed = make_param_shadow()()
    print("param_shadow_read:", shadowed)

main()
