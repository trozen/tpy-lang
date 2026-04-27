#pragma once

// Forward-declare A. The whole point of this regression test: <x/s.hpp>
// is the user-written native header for module `s`, and it deliberately
// does NOT include <x/a.hpp> -- standard C++ practice for headers that
// only need a name in a method signature. The complete A definition
// must therefore be pulled in by the consumer through the # tpy:
// include() reach-propagation path.
namespace xcore { struct A; }

namespace xcore {

struct S {
    A outer() const;
};

}  // namespace xcore
