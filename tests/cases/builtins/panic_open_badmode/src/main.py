# Test that open() panics for unsupported mode strings
def main() -> None:
    f = open("tpy_test_badmode.txt", "z")
    f.close()

main()
