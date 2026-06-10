# list() of a generator yielding a cross-module Own[Point]. Point is reached
# only via points()'s return type (never imported by name), so its C++ element
# name must be namespace-qualified in the construct<std::vector<...>> template
# the list() constructor expands -- otherwise the generated C++ fails to build.
from shapes import points

def main() -> None:
    out = list(points())
    for p in out:
        print(p.x)

main()
