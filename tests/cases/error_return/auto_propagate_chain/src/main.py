# Auto-propagation through a chain of @error_return functions (3 levels deep)
from tpy import int32, error_return, ReturnException

class NotFound(Exception, ReturnException):
    pass

@error_return(NotFound)
def lookup(items: list[int32], target: int32) -> int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

@error_return(NotFound)
def lookup_twice(items: list[int32], a: int32, b: int32) -> int32:
    ia = lookup(items, a)
    ib = lookup(items, b)
    return ia + ib

@error_return(NotFound)
def lookup_sum(items: list[int32], targets: list[int32]) -> int32:
    total: int32 = 0
    for t in targets:
        idx = lookup_twice(items, t, t)
        total += idx
    return total

def main() -> None:
    items: list[int32] = [10, 20, 30]

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
