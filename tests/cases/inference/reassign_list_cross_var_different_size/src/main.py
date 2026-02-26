# Cross-variable different-size reassignment promotes both to list
def main() -> None:
    a = [1, 2, 3]  # tpyc: type(/list/)
    b = [4, 5]     # tpyc: type(/list/)
    a = b
    print(len(a))
main()
