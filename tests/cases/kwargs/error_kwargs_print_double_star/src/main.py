# `**kw` at a print dispatches on the keyword NAMES at runtime, while print's
# sep=/end=/file=/flush= are each resolved to a different piece of the emitted
# chain at compile time. Rejected rather than rendered.
def main() -> None:
    kw = {"sep": "-"}
    print(1, 2, **kw)  # tpyc: error(/not yet supported/)


main()
