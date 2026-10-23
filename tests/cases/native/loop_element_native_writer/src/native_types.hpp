#pragma once
#include <cstdint>

// Writers of a TPy record they take by reference.
namespace nw {
template<class T> void bump(T& p) { p.n += 1; }
template<class T> int32_t bumped(T& p) { return ++p.n; }
}
