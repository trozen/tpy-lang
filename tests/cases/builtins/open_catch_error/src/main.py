# Test that open() raises FileNotFoundError (catchable) for missing files
def main() -> None:
    # Catch FileNotFoundError
    try:
        f = open("/nonexistent/path/file.txt")
        f.close()
    except FileNotFoundError:
        print("caught FileNotFoundError")

    # Catch via base class OSError
    try:
        f2 = open("/nonexistent/path/file2.txt")
        f2.close()
    except OSError:
        print("caught OSError")

    # Binary mode also throws
    try:
        f3 = open("/nonexistent/path/file3.bin", "rb")
        f3.close()
    except FileNotFoundError:
        print("caught binary FileNotFoundError")

    print("done")

main()
