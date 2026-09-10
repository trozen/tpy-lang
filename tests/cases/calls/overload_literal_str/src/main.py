# Literal string overload dispatch: open() returns different types based on mode literal
# open("path", "r") -> TextIO, open("path", "rb") -> BinaryIO

def main() -> None:
    # Literal "w" -> TextIO (text mode overload)
    f1 = open("tpy_literal_test.txt", "w")  # tpyc: type(TextIO)
    f1.write("hello")
    f1.close()

    # Literal "r" -> TextIO
    f2 = open("tpy_literal_test.txt", "r")  # tpyc: type(TextIO)
    print(f2.read())
    f2.close()

    # Literal "wb" -> BinaryIO (binary mode overload)
    f3 = open("tpy_literal_test.bin", "wb")  # tpyc: type(BinaryIO)
    f3.close()

    # Literal "rb" -> BinaryIO
    f4 = open("tpy_literal_test.bin", "rb")  # tpyc: type(BinaryIO)
    f4.close()

    # Variable (not a literal) falls through to str fallback -> TextIO
    mode = "r"
    f5 = open("tpy_literal_test.txt", mode)  # tpyc: type(TextIO)
    f5.close()

    print("ok")


main()
