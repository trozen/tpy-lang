# A SAME-module generator delegation inside a module that participates in an
# admissible import cycle. `cycle_peers` is self-inclusive, so the gate that
# rejects a mutually-infinite-size cross-module delegation must not fire here.
import moda


def main() -> None:
    for v in moda.free_delegator():
        print("free:", v)
    for v in moda.method_delegator(moda.Src(7)):
        print("method:", v)
    print("cycle:", moda.ping(3))


main()
