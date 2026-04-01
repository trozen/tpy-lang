# Test FileNotFoundError.message field and str() conversion
def main() -> None:
    try:
        f = open("/nonexistent/path/file.txt")
        f.close()
    except FileNotFoundError as e:
        print(e.message)
        print(str(e))

main()
