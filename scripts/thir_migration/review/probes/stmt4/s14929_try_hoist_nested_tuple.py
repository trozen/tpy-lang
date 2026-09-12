from tpy import int32
def probe(n: int32) -> int32:
    if n < 0:
        raise ValueError("neg")
    return n
def run(n: int32) -> int32:
    try:
        pair = ((1, 2), probe(n))
    except ValueError:
        return -1
    return pair[1]
def main() -> None:
    print(run(1))
main()
