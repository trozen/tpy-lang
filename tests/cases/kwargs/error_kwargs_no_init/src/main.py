# Error: kwargs on record without __init__

class Bare:
    x: int
    y: int

def main() -> None:
    b = Bare(x=1, y=2)  # tpyc: error(/unexpected keyword argument/)

main()
