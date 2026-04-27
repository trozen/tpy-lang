# print(..., file=opened_textio) goes through the as_ostream(TextFile&)
# direct-ostream path (no streambuf adapter).
def main() -> None:
    path = "/tmp/tpy_test_print_file_textio.txt"

    f = open(path, "w")
    print("hello", "file", 42, file=f)
    print("line 2", file=f, sep="-", end="!\n")
    f.close()

    with open(path) as r:
        print(r.read(), end="")

main()
