"""
fprime.build:

Sets up a singleton that handles the calls for build system help. This will allow for support of future build systems
that are not CMake.
"""
from .cmake import CMakeHandler

# Builder is a singleton supported by a CMakeHandler
builder = CMakeHandler()