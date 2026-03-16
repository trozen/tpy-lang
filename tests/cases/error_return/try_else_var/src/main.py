# Variable declared after error_return call in try body, used in else
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
        idx = find(items, 20)
        doubled = idx * 2
    except NotFound:
        print("not found")
    else:
        print(doubled)

main()
