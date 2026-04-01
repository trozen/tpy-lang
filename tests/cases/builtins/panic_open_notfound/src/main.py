# Test that unhandled FileNotFoundError from open() terminates the program
def main() -> None:
    f = open("/nonexistent/path/file.txt")
    f.close()

main()
