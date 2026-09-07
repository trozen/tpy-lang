# A pointer-slot record global of another module bound to a plain local: the
# decl is a sink with no pinned consumer, so it rejects. The pinned receiver,
# iterable and `del` positions are in
# tests/cases/globals/module_var_record_receiver.
import store


def bind_and_write() -> None:
    e = store.env  # tpyc: error(/decl\.slot_type/)
    e["b"] = "y"


def main() -> None:
    bind_and_write()
    print(store.env["b"])


main()
