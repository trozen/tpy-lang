# Cross-variable same-size reassignment without mutation stays Array
def main() -> None:
    a = [1, 2, 3]  # tpyc: type(/Array/)
    b = [4, 5, 6]  # tpyc: type(/Array/)
    a = b
    print(len(a))
main()
