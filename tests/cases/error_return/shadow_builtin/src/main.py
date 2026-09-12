# User-defined class shadows builtin StopIteration -- should use the user's type, not ::tpy::StopIteration
from tpy import int32, error_return, ReturnException

class StopIteration(Exception, ReturnException):
    pass

@error_return(StopIteration)
def first_negative(items: list[int32]) -> int32:
    for i in range(len(items)):
        if items[i] < 0:
            return items[i]
    raise StopIteration

def main() -> None:
    try:
        v = first_negative([1, -2, 3])
    except StopIteration:
        print("none")
    else:
        print(v)

    try:
        v2 = first_negative([1, 2, 3])
    except StopIteration:
        print("none")
    else:
        print(v2)

main()
