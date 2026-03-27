# Auto-propagation through a chain of @error_return functions (3 levels deep)
from tpy import Int32, error_return, ControlFlow

class NotFound(Exception, ControlFlow):
    pass

@error_return(NotFound)
def lookup(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

@error_return(NotFound)
def lookup_twice(items: list[Int32], a: Int32, b: Int32) -> Int32:
    ia = lookup(items, a)
    ib = lookup(items, b)
    return ia + ib

@error_return(NotFound)
def lookup_sum(items: list[Int32], targets: list[Int32]) -> Int32:
    total: Int32 = 0
    for t in targets:
        idx = lookup_twice(items, t, t)
        total += idx
    return total

def main() -> None:
    items: list[Int32] = [10, 20, 30]

    # All found
    try:
        v = lookup_sum(items, [10, 20])
    except NotFound:
        print("not found")
    else:
        print(v)

    # Second target missing
    try:
        v2 = lookup_sum(items, [10, 99])
    except NotFound:
        print("not found")
    else:
        print(v2)

main()
