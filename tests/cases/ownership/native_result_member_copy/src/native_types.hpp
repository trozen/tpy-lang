#pragma once

namespace nb {
// A by-value result that is a copy of the argument: it may be a copy of a
// borrow, so a member of it copies an object the program still reaches.
template<class T> T copy_of(const T& t) { return t; }
}
