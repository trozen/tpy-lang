# Send[T]/Sync[T] marker wrappers: non-erased types erase at resolve time
# (Send[int32] == int32); erased Callable persists sema-side but is
# C++-identical to bare Callable (take_marked vs take_bare in the snapshot).
from tpy import int32, Send, Sync
from typing import Callable

def take_marked(cb: Send[Callable[[int32], None]]) -> None:
    cb(1)

def take_bare(cb: Callable[[int32], None]) -> None:
    cb(1)

def main() -> None:
    x: Send[int32] = 5              # tpyc: type(int32) is_send(yes)
    xs: Send[list[int32]] = [1, 2]  # tpyc: type(list[int32]) is_sync(no)
    y: Sync[int32] = 6              # tpyc: type(int32) is_sync(yes)
    take_bare(lambda n: print(n))
    print(x, len(xs), y)

main()
