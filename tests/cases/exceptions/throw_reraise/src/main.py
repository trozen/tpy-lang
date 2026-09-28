# Re-raise: bare raise inside except block re-throws
from tpy import int32

class MyError(Exception):
    code: int32

    def __init__(self, code: int32) -> None:
        super().__init__()
        self.code = code

def fail() -> None:
    raise MyError(7)

def middle() -> None:
    try:
        fail()
    except MyError as e:
        print(e.code)
        raise

def main() -> None:
    try:
        middle()
    except MyError as e:
        print(e.code)

main()
