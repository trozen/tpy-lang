# Error: print() requires flush= to be a bool literal (no runtime values yet).
def main() -> None:
    flag = True
    print("hello", flush=flag)  # tpyc: error(/'flush' argument must be a bool literal/)

main()
