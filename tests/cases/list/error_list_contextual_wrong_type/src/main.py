# Annotated list literal rejects elements incompatible with the annotation.
from tpy import int32

def main():
    a: list[int32 | None] = ["hello"]  # tpyc: error(/incompatible with annotated element type/)

main()
