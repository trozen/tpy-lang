# A subclass object handed over as its base: a live one is copied, the
# body's own dying one moved.
from part_subclass import VSub, show_v, main as subclass_main
from part_values import main as values_main
from part_handover import main as handover_main
from part_targets import main as targets_main


# module level: a value-type global handed to a union parameter is copied
# into the base, the global keeping its value
GVS = VSub("a-global-string-long-enough-to-own-a-heap-buffer")
show_v("global-vsub", GVS)  # tpyc: ok
print("global-vsub-kept", GVS.s)


def main() -> None:
    subclass_main()
    values_main()
    handover_main()
    targets_main()


main()
