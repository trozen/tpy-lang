# Annotated list literal rejects elements incompatible with the annotation.
from tpy import Int32

def main():
    a: list[Int32 | None] = ["hello"]  # tpyc: error(/incompatible with annotated element type/)

main()
