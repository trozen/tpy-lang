# make_default() error: no type context to infer T
from tpy import make_default

def main() -> None:
    x = make_default()  # tpyc: error(/No matching overload/)
    print(x)

main()
