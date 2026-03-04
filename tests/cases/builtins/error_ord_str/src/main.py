# ord() rejects str argument (requires Char)
def main() -> None:
    s: str = "a"
    print(ord(s))  # tpyc: error(/No matching overload for ord/)

main()
