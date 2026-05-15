# Regression: list[RefType].pop() in a generic context. Previously failed
# because `list.pop` returned bare `T` and codegen emitted
# `val_or_ref_t<T> lastelt = pop_back(heap)` -- for non-value T this
# resolves to `T&` and can't bind to pop_back's rvalue return. Fixed by
# annotating `list.pop` to return `Own[T]`, making the ownership transfer
# explicit (codegen routes Own[T] returns to plain T storage).
import heapq


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v

    def __lt__(self, o: 'Box') -> bool:
        return self.val < o.val


def main() -> None:
    heap: list[Box] = [Box(3), Box(1), Box(2)]
    heapq.heapify(heap)
    top = heapq.heappop(heap)
    print(top.val)
    second = heap.pop()
    print(second.val)

    items: list[Box] = [Box(10), Box(20), Box(30), Box(40)]
    middle = items.pop(1)
    print(middle.val)
    last = items.pop(-1)
    print(last.val)
    print(len(items))


main()
