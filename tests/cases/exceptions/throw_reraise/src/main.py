# Re-raise: bare raise inside except block re-throws
from tpy import Int32

class MyError(Exception):
    code: Int32

    def __init__(self, code: Int32) -> None:
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
