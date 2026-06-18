#pragma once

// Filesystem + environment primitives backing the `os` / `os.path` stdlib
// modules. Declarations only: the bodies (and the heavy <filesystem> /
// <sys/stat.h> includes they need) live in src/stdlib/os_impl.cpp so those
// system headers do not leak into every TU that calls into os.

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace tpy::stdlib::os {

std::string getcwd();
void chdir(std::string_view path);
std::vector<std::string> listdir(std::string_view path);

// Status-query predicates swallow OS errors and report False (matching
// os.path.exists/isfile/isdir/islink/lexists).
bool path_exists(std::string_view path);
bool path_lexists(std::string_view path);
bool path_isfile(std::string_view path);
bool path_isdir(std::string_view path);
bool path_islink(std::string_view path);

// getsize raises OSError for a missing path (unlike the predicates).
int64_t path_getsize(std::string_view path);

bool env_has(std::string_view key);
std::string env_get(std::string_view key);

} // namespace tpy::stdlib::os
