# Module-level code compiles into __tpy_init(), which runs the same
# pending-resolution pass as a function body. Empty list/dict literals
# at module scope should be able to infer their element type the same
# way they do inside a function -- no explicit annotation required when
# the usage pins the type.

# Empty list, element type inferred from call-site context (sum's
# default-int-typed overload wins at cost 0).
print(sum([]))  # tpyc: ok

# Empty list, element type inferred from subsequent appends.
xs = []  # tpyc: ok
xs.append(1)
xs.append(2)
print(xs)

# Empty dict, key/value inferred from subsequent assignment.
d = {}  # tpyc: ok
d[1] = "one"
d[2] = "two"
print(d)
