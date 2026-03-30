# Error: raise inside finally is not yet supported
def main() -> None:
    try:
        print("try")
    finally:
        raise ValueError("oops")  # tpyc: error(/raise.*inside.*finally.*not yet supported/)

main()
