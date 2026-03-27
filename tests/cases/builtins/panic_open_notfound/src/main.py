# Test that open() panics when file does not exist
def main() -> None:
    f = open("/nonexistent/path/file.txt")
    f.close()

main()
