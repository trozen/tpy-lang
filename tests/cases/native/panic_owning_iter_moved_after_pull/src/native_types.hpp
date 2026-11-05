#pragma once
#include <tpy/tpy.hpp>
#include <string>
#include <utility>

namespace probe {

// The cursor points into the string; after a move it would walk freed
// storage, and restarting would replay the pulled element.
inline auto moved_prefix() {
    auto it = tpy::__iter__(std::string(40, 'a') + "bcd");
    (void)it.__next__();
    auto moved = std::move(it);
    return moved;
}

} // namespace probe
