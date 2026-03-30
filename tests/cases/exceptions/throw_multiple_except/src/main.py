# Multiple except handlers with type matching
class MyError(Exception):
    pass

def throw_value_error() -> None:
    raise ValueError("val")

def throw_my_error() -> None:
    raise MyError

def main() -> None:
    # First handler matches
    try:
        throw_value_error()
    except ValueError:
        print("caught ValueError")
    except MyError:
        print("caught MyError")

    # Second handler matches
    try:
        throw_my_error()
    except ValueError:
        print("caught ValueError")
    except MyError:
        print("caught MyError")

main()
