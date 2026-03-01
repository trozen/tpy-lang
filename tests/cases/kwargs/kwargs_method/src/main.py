# Keyword arguments in method calls

class Formatter:
    prefix: str
    def __init__(self) -> None:
        self.prefix = ">"

    def format(self, text: str, width: int = 0, fill: str = " ") -> str:
        result = self.prefix + text
        return result

def main() -> None:
    fmt = Formatter()
    print(fmt.format("hello"))
    print(fmt.format("hello", fill="*"))
    print(fmt.format(text="world", width=10))

main()
