from tpy import int32, Span, Array

def main() -> None:
    matrix: Array[Array[int32, 2], 2] = [[1, 2], [3, 4]]
    s: Span[int32] = matrix[1]
    print(s[0])
    print(s[1])

main()
