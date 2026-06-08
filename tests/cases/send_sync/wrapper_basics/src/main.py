# Send[T]/Sync[T] marker wrappers: non-erased types erase at resolve time
# (Send[Int32] == Int32); erased Callable persists sema-side but is
# C++-identical to bare Callable (take_marked vs take_bare in the snapshot).
from tpy import Int32, Send, Sync
from typing import Callable

def take_marked(cb: Send[Callable[[Int32], None]]) -> None:
    cb(1)

def take_bare(cb: Callable[[Int32], None]) -> None:
    cb(1)

def main() -> None:
    x: Send[Int32] = 5              # tpyc: type(Int32) is_send(yes)
    xs: Send[list[Int32]] = [1, 2]  # tpyc: type(list[Int32]) is_sync(no)
    y: Sync[Int32] = 6              # tpyc: type(Int32) is_sync(yes)
    take_bare(lambda n: print(n))
    print(x, len(xs), y)

main()
