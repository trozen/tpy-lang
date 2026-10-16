# A comparison with the record on the right asks its reflected dunder:
# `3 < g` needs `G.__gt__`, which G does not define.
class G:
    def __init__(self) -> None:
        pass


def main() -> None:
    g = G()
    print(3 < g)  # tpyc: error(/Comparison '<' on 'G': no '__gt__' method defined/)


main()
