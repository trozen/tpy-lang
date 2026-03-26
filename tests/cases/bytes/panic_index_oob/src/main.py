# Out-of-bounds bytes index panics at runtime
def main() -> None:
    data = b"abc"
    print(data[10])

main()
