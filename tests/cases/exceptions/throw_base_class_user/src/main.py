# except Exception catches user-defined exception (polymorphic C++ catch)
from tpy import int32

class AppError(Exception):
    code: int32

    def __init__(self, code: int32) -> None:
        self.code = code

def main() -> None:
    try:
        raise AppError(7)
    except Exception:
        print("caught as Exception")

main()
