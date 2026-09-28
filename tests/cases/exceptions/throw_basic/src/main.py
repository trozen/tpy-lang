# Basic throw/catch: raise non-ReturnException exception, catch with try/except
from tpy import int32

class MyError(Exception):
    code: int32

    def __init__(self, code: int32) -> None:
        super().__init__()
        self.code = code

def fail() -> None:
    raise MyError(42)

def main() -> None:
    try:
        fail()
    except MyError:
        print("caught MyError")

    # With binding and field access
    try:
        fail()
    except MyError as e:
        print(e.code)

    # Raise with no args (default construction)
    try:
        raise ValueError
    except ValueError:
        print("caught bare ValueError")

main()
