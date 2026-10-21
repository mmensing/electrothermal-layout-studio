# SPDX-License-Identifier: MPL-2.0
# Build the upstream Qt-independent runtime target, not the generator or bindings.
cmake_minimum_required(VERSION 3.26)
project(etlayout_shiboken_runtime LANGUAGES CXX)
if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "Use a native x64 MSVC Windows toolchain; MinGW is ABI-incompatible with the PySide wheels")
endif()
if(NOT DEFINED PYSIDE_SOURCE OR NOT EXISTS "${PYSIDE_SOURCE}/sources/shiboken6/libshiboken/CMakeLists.txt")
    message(FATAL_ERROR "PYSIDE_SOURCE must be the verified, patched 6.12.0 source root")
endif()
find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module Development.SABIModule)
if(NOT Python_VERSION_MAJOR EQUAL 3 OR NOT Python_VERSION_MINOR EQUAL 12)
    message(FATAL_ERROR "This application profile is tested with CPython 3.12")
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type")
add_compile_options(/Zc:wchar_t /GR /EHsc /DWIN32 /D_WINDOWS /D_SCL_SECURE_NO_WARNINGS /Brepro)
# Avoid personal absolute checkout/build paths in __FILE__, diagnostics and PDBs.
file(TO_NATIVE_PATH "${PYSIDE_SOURCE}" source_native)
file(TO_NATIVE_PATH "${CMAKE_BINARY_DIR}" build_native)
add_compile_options("/pathmap:${source_native}=pyside-source" "/pathmap:${build_native}=shiboken-build")
add_link_options(/Brepro "/PDBALTPATH:%_PDB%")
include("${PYSIDE_SOURCE}/sources/shiboken6/.cmake.conf")
include("${PYSIDE_SOURCE}/sources/shiboken6/cmake/ShibokenHelpers.cmake")
set(PYTHON_LIMITED_API 1)
set(SHIBOKEN_PYTHON_LIBRARIES "${Python_SABI_LIBRARIES}")
if(NOT SHIBOKEN_PYTHON_LIBRARIES)
    message(FATAL_ERROR "The CPython stable-ABI import library python3.lib is required")
endif()
set(PYTHON_SHARED_LIBRARY_SUFFIX ".abi3")
set(shiboken6_library_so_version "6.12")
set(LIB_INSTALL_DIR "lib")
set(SHIBOKEN_IS_CROSS_BUILD FALSE)
set(QUIET_BUILD TRUE)
set(PYTHON_WITH_DEBUG FALSE)
set(PYTHON_WITH_COUNT_ALLOCS FALSE)
set(is_pyside6_superproject_build TRUE)
set(shiboken_python_files
    "signature/lib/__init__.py" "signature/lib/enum_sig.py"
    "signature/lib/pyi_generator.py" "signature/lib/tool.py"
    "signature/__init__.py" "signature/errorhandler.py" "signature/importhandler.py"
    "signature/layout.py" "signature/loader.py" "signature/mapping.py"
    "signature/parser.py" "__init__.py" "feature.py")
execute_process(COMMAND "${Python_EXECUTABLE}" -c "import numpy; print(numpy.get_include())"
    OUTPUT_VARIABLE NUMPY_INCLUDE_DIR OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
add_subdirectory("${PYSIDE_SOURCE}/sources/shiboken6/libshiboken" libshiboken)
