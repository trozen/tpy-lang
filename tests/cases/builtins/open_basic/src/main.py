# Test open() builtin: write, read, readlines, readline, with statement
def main() -> None:
    path = "/tmp/tpy_test_open_basic.txt"

    # Write to file
    w = open(path, "w")
    w.write("alpha\nbeta\ngamma")
    w.close()

    # Read entire file
    r = open(path)
    print(r.read())
    r.close()

    # Readlines count
    r2 = open(path)
    lines = r2.readlines()
    print(len(lines))
    r2.close()

    # Readline lengths (alpha\n=6, beta\n=5, gamma=5)
    r3 = open(path)
    first = r3.readline()
    second = r3.readline()
    third = r3.readline()
    print(len(first), len(second), len(third))
    r3.close()

    # With statement (context manager)
    with open(path) as f1:
        print(f1.read())

    # Append mode
    a = open(path, "a")
    a.write("\ndelta")
    a.close()

    with open(path) as f2:
        print(f2.read())

main()
