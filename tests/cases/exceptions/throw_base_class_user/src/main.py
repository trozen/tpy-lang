# except Exception catches user-defined exception (polymorphic C++ catch)
from tpy import Int32

class AppError(Exception):
    code: Int32

    def __init__(self, code: Int32) -> None:
        self.code = code

def main() -> None:
    try:
        raise AppError(7)
    except Exception:
        print("caught as Exception")

main()
