from tpy import Int32, Span, Array

def main() -> None:
    matrix: Array[Array[Int32, 2], 2] = [[1, 2], [3, 4]]
    s: Span[Int32] = matrix[1]
    print(s[0])
    print(s[1])

main()
