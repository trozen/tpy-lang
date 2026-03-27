# Test that open() panics for unsupported mode strings
def main() -> None:
    f = open("/tmp/tpy_test_badmode.txt", "rb")
    f.close()

main()
