# Lambda with string parameters.
from tpy import Fn

def transform(f: Fn[[str], str], s: str) -> str:
    return f(s)

def main() -> None:
    print(transform(lambda s: s + "!", "hello"))
    print(transform(lambda s: s + s, "ab"))

main()
