# Cross-variable same-size reassignment: mutation on one promotes both
def main() -> None:
    a = [1, 2, 3]  # tpyc: type(/list/)
    b = [4, 5, 6]  # tpyc: type(/list/)
    a = b
    a.append(7)
    print(len(b))
main()
