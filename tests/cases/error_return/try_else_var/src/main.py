# Variable declared after error_return call in try body, used in else
from tpy import int32, error_return, ReturnException

class NotFound(Exception, ReturnException):
    pass

@error_return(NotFound)
def find(items: list[int32], target: int32) -> int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

def main() -> None:
    items: list[int32] = [10, 20, 30]

    try:
        idx = find(items, 20)
        doubled = idx * 2
    except NotFound:
        print("not found")
    else:
        print(doubled)

main()
