# List alias without mutation stays Array
def main() -> None:
    a = [1, 2, 3]  # tpyc: type(/Array/)
    b = a           # tpyc: type(/Array/)
    print(len(b))

main()
