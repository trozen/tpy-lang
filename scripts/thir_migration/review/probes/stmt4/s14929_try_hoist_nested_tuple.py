from tpy import Int32
def probe(n: Int32) -> Int32:
    if n < 0:
        raise ValueError("neg")
    return n
def run(n: Int32) -> Int32:
    try:
        pair = ((1, 2), probe(n))
    except ValueError:
        return -1
    return pair[1]
def main() -> None:
    print(run(1))
main()
