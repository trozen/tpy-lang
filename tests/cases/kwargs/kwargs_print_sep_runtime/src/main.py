# print() sep= and end= accept runtime string values, not just literals.
def main() -> None:
    delim = ", "
    print("a", "b", "c", sep=delim)

    suffix = "!\n"
    print("hello", end=suffix)

    s = ":"
    print("x", "y", sep=s, end=suffix)

    # Empty separator at runtime.
    e = ""
    print("p", "q", sep=e)

main()
