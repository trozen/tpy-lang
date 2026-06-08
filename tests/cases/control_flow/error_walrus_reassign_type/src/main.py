# Walrus reassignment of an existing local to an incompatible type is rejected
# at sema, like the equivalent plain reassignment.
def main() -> None:
    s = "asd"
    if (s := 5):  # tpyc: error(/Type mismatch in reassignment to 's': expected str/)
        print(s)

main()
