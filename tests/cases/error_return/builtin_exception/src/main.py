# @error_return with builtin StopIteration type (emits ::tpy::StopIteration in C++)
from tpy import Int32, error_return

@error_return(StopIteration)
def parse_positive(s: str) -> Int32:
    if s == "two":
        return 2
    raise StopIteration

def main() -> None:
    try:
        v = parse_positive("two")
    except StopIteration:
        print("error")
    else:
        print(v)

    try:
        v2 = parse_positive("three")
    except StopIteration:
        print("error")
    else:
        print(v2)

main()
