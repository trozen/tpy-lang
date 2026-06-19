# `message` is a field of the builtin Exception base; a subclass assigning
# self.message must reuse that inherited slot, not declare a shadow.
class AppError(Exception):
    def __init__(self, message: str):
        self.message = message
    def detail(self) -> str:
        return self.message

def main() -> None:
    try:
        raise AppError("boom")
    except AppError as e:
        print(e.detail())

main()
