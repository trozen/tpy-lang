# Pop from empty bytearray panics at runtime
def main() -> None:
    ba = bytearray()
    ba.pop()

main()
