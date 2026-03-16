# @error_return call nested inside if/else within try body
from tpy import Int32, error_return

class NotFound(Exception):
    pass

@error_return(NotFound)
def find(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

def main() -> None:
    items: list[Int32] = [10, 20, 30]

    try:
        if len(items) > 0:
            idx = find(items, 20)
        else:
            idx = find(items, 10)
    except NotFound:
        print("not found")
    else:
        print(idx)

main()
