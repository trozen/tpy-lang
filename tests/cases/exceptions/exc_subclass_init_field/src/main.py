# An Exception subclass declares a data field via __init__ (no class annotation),
# then raises, catches, and reads it.
class AppError(Exception):
    def __init__(self, code: int):
        super().__init__()
        self.code = code

def main() -> None:
    try:
        raise AppError(7)
    except AppError as e:
        print(e.code)

main()
