# `WIDTH` is a statement-level module binding and gets a module slot;
# `LOOPY` is first bound inside a top-level `for` body, so it is a local of
# the generated module-init function with no slot to export.
from tpy import int32

WIDTH = 800

for i in range(3):
    LOOPY = i * 2  # tpyc: warning(/ALL_CAPS variable 'LOOPY'/)
