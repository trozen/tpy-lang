# @error_return decorator: function returns error via std::expected, caller uses try/except
from tpy import Int32, error_return, ReturnException

class NotFound(Exception, ReturnException):
    pass

@error_return(NotFound)
def find_index(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

def main() -> None:
    items: list[Int32] = [10, 20, 30, 40]

    # Success case
    try:
        idx = find_index(items, 30)
    except NotFound:
        print("not found")
    else:
        print(idx)

    # Error case
    try:
        idx2 = find_index(items, 99)
    except NotFound:
        print("not found")
    else:
        print(idx2)

main()
