# Panic: slice step cannot be zero.
def main() -> None:
    s: str = "hello"
    print(s[::0])

main()
