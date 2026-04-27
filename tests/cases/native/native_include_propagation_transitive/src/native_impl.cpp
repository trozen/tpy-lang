// Provides the link-time definition of S::outer().
// Includes both headers itself, so this TU compiles cleanly regardless
// of what the generated main.cpp pulls in -- the test only exercises
// the consumer-side include set.
#include "x/a.hpp"
#include "x/s.hpp"

namespace xcore {

A S::outer() const {
    return A{};
}

}  // namespace xcore
