# @error_return with builtin Exception type (emits ::tpy::Exception in C++)
from tpy import Int32, error_return

@error_return(Exception)
def parse_positive(s: str) -> Int32:
    if s == "two":
        return 2
    raise Exception

def main() -> None:
    try:
        v = parse_positive("two")
    except Exception:
        print("error")
    else:
        print(v)

    try:
        v2 = parse_positive("three")
    except Exception:
        print("error")
    else:
        print(v2)

main()
