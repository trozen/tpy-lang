# return inside try-with-finally: finally runs before actual return
from tpy import Int32

def return_from_try() -> Int32:
    try:
        return 42
    finally:
        print("finally 1")

def return_from_except() -> Int32:
    try:
        raise ValueError("err")
    except ValueError:
        return 99
    finally:
        print("finally 2")

def return_from_multiple_paths(flag: bool) -> str:
    try:
        if flag:
            return "yes"
        for i in range(3):
            if i == 1:
                return "loop"
        return "default"
    finally:
        print("finally 3")

def return_optional(flag: bool) -> Int32 | None:
    """Value-type Optional return with try/finally."""
    try:
        if flag:
            return Int32(7)
        return None
    finally:
        print("finally 4")

def main() -> None:
    print(return_from_try())
    print(return_from_except())
    print(return_from_multiple_paths(True))
    print(return_from_multiple_paths(False))
    r = return_optional(True)
    if r is not None:
        print(r)
    r2 = return_optional(False)
    if r2 is None:
        print("none")

main()
