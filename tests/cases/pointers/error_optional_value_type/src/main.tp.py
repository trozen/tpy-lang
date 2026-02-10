from tpy import Int32

def main() -> None:
    x: Int32 | None = None  # tpyc: error(/not yet supported/)
