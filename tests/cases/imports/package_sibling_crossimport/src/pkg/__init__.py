# Re-export records, an enum, a generic record, a @dynamic protocol,
# a function, and a variable from sibling submodules of this package.
# pkg.a additionally imports B from pkg.b. Before the sibling-cycle
# fix, pkg/a.cpp failed to compile because pkg.hpp's
# `using ::tpyapp::pkg::a::A;` ran while pkg::a was still mid-parsing.
from pkg.a import A, Container, f
from pkg.b import B, K, V, Greeter
