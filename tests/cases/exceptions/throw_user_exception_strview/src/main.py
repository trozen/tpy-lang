# User Exception subclass with `str` (string_view) message param
# delegating to the native BaseException ctor via super().__init__.
# Mirrors the idiomatic Python pattern (vs the throw_user_exception
# variant which only stores ad-hoc fields).
class MyError(Exception):
    msg: str

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.msg = message

def main() -> None:
    try:
        raise MyError("boom")
    except MyError as e:
        print(e.msg)

    try:
        raise MyError()
    except MyError as e:
        print("empty:", e.msg)

main()
