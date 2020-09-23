"""
fprime.util.build_helper.py

This script is defined to help users run standard make processes using CMake. This will support migrants from the old
make system, as well as enable more efficient practices when developing in the CMake system. The following functions
are supported herein:

 - build: build the current directory/module/deployment
 - impl: make implementation templates
 - testimpl: make testing templates
 - build_ut: build the current UTs
 - check: run modules unit tests

@author mstarch
"""
import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from fprime.common.error import FprimeException
from fprime.fbuild.builder import (
    Target,
    LocalTarget,
    Build,
    BuildType,
    NoValidBuildTypeException,
)


CMAKE_REG = re.compile(r"-D([a-zA-Z0-9_]+)=([a-zA-Z0-9_]+)")


def get_target(parsed: argparse.Namespace) -> Target:
    """Gets the target given the argparse namespace

    Takes the parsed namespace and processes it to a known matching target.

    Args:
        parsed: argparse namespace to read values from

    Returns:
        target that can support the given supplied namespace
    """
    mnemonic = parsed.command
    flags = {
        flag for flag in Target.get_all_possible_flags() if getattr(parsed, flag, False)
    }
    return Target.get_target(mnemonic, flags)


def get_build(
    parsed: argparse.Namespace, deployment: Path, verbose: bool, target: Target = None
) -> Build:
    """Gets the build given the namespace

    Takes the parsed namespace and processes it to a known build configuration. This will refine down a supplied list of
    build types or loop through all of the build types if None is specified.

    Args:
        parsed: argparse namespace to read values from
        deployment: deployment directory the build will operate against
        verbose: have we enabled verbose output
        build_types: build types to search through

    Returns:
        build meeting the specifications on build type
    """
    build_types = (
        target.build_types
        if target is not None
        else [build_type for build_type in BuildType]
    )
    build_type = [
        build_type
        for build_type in build_types
        if parsed.build_type is None
        or build_type.get_cmake_build_type() == parsed.build_type
    ]
    # None found, explode
    if not build_type:
        raise NoValidBuildTypeException(
            "Could not execute '{}' with a build type '{}'".format(
                target, parsed.build_type
            )
        )
    # Grab first build when multiple are available
    return Build(build_type[0], deployment, verbose=verbose)


def validate(parsed, unknown):
    """
    Validate rules to ensure that the args are properly consistent. This will also generate a set of validated arguments
    to pass to CMake. This allows these values to be created, defaulted, and validated in one place
    :param parsed: args to validate
    :param unknown: unknown arguments
    :return: cmake arguments to pass to CMake
    """
    cmake_args = {}
    make_args = {}
    # Check platforms for existing toolchain, unless the default is specified.
    if parsed.command == "generate":
        D_ARGS = {
            match.group(1): match.groups(2)
            for match in [CMAKE_REG.match(arg) for arg in unknown]
        }
        cmake_args.update(D_ARGS)
    # Build type only for generate, jobs only for non-generate
    elif (
        parsed.command != "info"
        and parsed.command != "purge"
        and parsed.command != "hash-to-file"
    ):
        parsed.settings = None  # Force to load from cache if possible
        make_args.update({"--jobs": (1 if parsed.jobs <= 0 else parsed.jobs)})
    return cmake_args, make_args


def find_toolchain(platform, path, build: Build):
    """
    Finds a toolchain for the given platform.  Searches in known locations for the toolchain, and compares against F
    prime provided toolchains
    :param platform: platform supplied by user for finding toolchain automatically
    :param path: path to the CMakeLists.txt directory, which acts as a default location if project_root not set
    :return: toolchain file
    """
    default_toolchain = build.get_settings("default_toolchain", "native")
    toolchain_locations = build.get_settings(
        ["framework_path", "project_root"], [None, path]
    )
    toolchain_locations += build.get_settings("library_locations", [])

    toolchain = default_toolchain if platform == "default" else platform
    # If toolchain is the native target, this is supplied by CMake and we exit here.
    if toolchain == "native":
        return None
    # Otherwise, find locations of toolchain files using the specified locations from settings.
    else:
        toolchains_paths = [
            os.path.join(loc, "cmake", "toolchain", toolchain + ".cmake")
            for loc in toolchain_locations
            if loc is not None
        ]
        toolchains = [
            toolchain_path
            for toolchain_path in toolchains_paths
            if os.path.exists(toolchain_path)
        ]
        if not toolchains:
            print(
                "[ERROR] Toolchain file {} does not exist at any of {}".format(
                    toolchain + ".cmake", ", ".join(list(toolchains_paths))
                )
            )
            sys.exit(-1)
        elif len(toolchains) > 1:
            print(
                "[ERROR] Toolchain file {} found multiple times. Aborting.".format(
                    toolchain + ".cmake"
                )
            )
            sys.exit(-1)
    return toolchains[0]


def add_target_parser(
    target: Target,
    subparsers,
    common: argparse.ArgumentParser,
    existing: Dict[str, Tuple[argparse.ArgumentParser, List[str]]],
):
    """Add a subparser for a given build target

    For a given build target, construct an argument parser with mnemonic as subcommand and needed flags. Then add it as
    a subparser to the global parent. If it already exists in existing, then just add non-existent flags/

    Args:
        target:     target for building a new subparser
        subparsers: argument parser to add a subparser to
        common:     common subparser to be used as parent carrying common flags
        existing:   dictionary storing the mnemonic to parser and flags tuple

    Notes:
        This functions has side effects of editing existing and the list of subparsers
    """
    if target.mnemonic not in existing:
        parser = subparsers.add_parser(
            target.mnemonic,
            parents=[common],
            add_help=False,
            help="{} in the specified directory".format(target.desc),
        )
        existing[target.mnemonic] = (parser, [])
        # Common target-only items
        parser.add_argument(
            "-j",
            "--jobs",
            default=1,
            type=int,
            help="Parallel build job count. Default: %(default)s.",
        )
    parser, flags = existing[target.mnemonic]
    new_flags = [flag for flag in target.flags if flag not in flags]
    for flag in new_flags:
        parser.add_argument(
            "--{}".format(flag), action="store_true", default=False, help=target.desc
        )
    flags.extend(new_flags)


def parse_args(args):
    """
    Parse the arguments to the CLI. This will then enable the user to run the above listed commands via the commands.
    :param args: CLI arguments to process
    :return: parsed arguments in a Namespace
    """
    # Common parser specifying common arguments input into the utility
    common_parser = argparse.ArgumentParser(
        description="Common Parser for Common Ingredients."
    )
    common_parser.add_argument(
        "platform",
        nargs="?",
        default="default",
        help="F prime build platform (e.g. Linux, Darwin). Default specified in settings.ini",
    )
    common_parser.add_argument(
        "-b",
        "--build-dir",
        dest="build_dir",
        default=None,
        help="Specific F prime build directory to use. Better to specify --deploy.",
    )
    common_parser.add_argument(
        "-d",
        "--deploy",
        dest="deploy",
        default=None,
        help="F prime deployment directory to use. May contain multiple build directories.",
    )
    common_parser.add_argument(
        "-p",
        "--path",
        default=os.getcwd(),
        help="F prime directory to operate on. Default: cwd, %(default)s.",
    )
    common_parser.add_argument(
        "--build-type",
        dest="build_type",
        default=None,
        choices=[build_type.get_cmake_build_type() for build_type in BuildType],
        help="CMake build type passed to CMake system.",
    )
    common_parser.add_argument(
        "-v",
        "--verbose",
        default=False,
        action="store_true",
        help="Turn on verbose output.",
    )

    # Main parser for the whole application
    parsers = {}
    parser = argparse.ArgumentParser(description="F prime helper application.")
    subparsers = parser.add_subparsers(
        description="F prime utility command line. Please run one of the commands. "
        + "For help, run a command with the --help flag.",
        dest="command",
    )
    # Add non-target parsers
    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate a build cache directory",
        parents=[common_parser],
        add_help=False,
    )
    generate_parser.add_argument(
        "-Dxyz",
        action="append",
        help="Pass -D flags through to CMakes",
        nargs=1,
        default=[],
    )
    purge_parser = subparsers.add_parser(
        "purge",
        help="Purge build cache directoriess",
        add_help=False,
        parents=[common_parser],
    )
    purge_parser.add_argument(
        "-f",
        "--force",
        default=False,
        action="store_true",
        help="Purges the build directory by force. No confirmation will be requested.",
    )
    # Add a search for hash function
    hash_parser = subparsers.add_parser(
        "hash-to-file",
        help="Converts F prime build hash to filename.",
        parents=[common_parser],
        add_help=False,
    )
    hash_parser.add_argument(
        "hash",
        type=lambda x: int(x, 0),
        help="F prime assert hash to associate with a file.",
    )

    # Add a search for hash function
    subparsers.add_parser(
        "info",
        help="Gets fprime-util contextual information.",
        parents=[common_parser],
        add_help=False,
    )
    for target in Target.get_all_targets():
        add_target_parser(target, subparsers, common_parser, parsers)
    # Parse and prepare to run
    parsed, unknown = parser.parse_known_args(args)
    bad = [bad for bad in unknown if not CMAKE_REG.match(bad)]
    if not hasattr(parsed, "command") or parsed.command is None:
        parser.print_help()
        sys.exit(1)
    elif bad:
        print("[ERROR] Unknown arguments: {}".format(", ".join(bad)))
        parser.print_help()
        sys.exit(1)
    cmake_args, make_args = validate(parsed, unknown)
    return parsed, cmake_args, make_args, parser


def confirm():
    """ Confirms the removal of the file with a yes or no input """
    # Loop "forever" intended
    while True:
        confirm_input = input("Purge this directory (yes/no)?")
        if confirm_input.lower() in ["y", "yes"]:
            return True
        elif confirm_input.lower() in ["n", "no"]:
            return False
        print("{} is invalid.  Please use 'yes' or 'no'".format(confirm_input))


def print_info(parsed, deployment):
    """ Builds and prints the informational output block """
    print("[INFO] Fprime build information:")
    build_types = [parsed.build_type] if parsed.build_type is not None else BuildType
    for build_type in build_types:
        build = Build(build_type, deployment, verbose=parsed.verbose)
        build.load(parsed.platform, parsed.build_dir)
        build_info = build.get_build_info(parsed.path)
        # Target list
        local_targets = [
            "'{}'".format(target) for target in build_info.get("local_targets", [])
        ]
        global_targets = [
            "'{}'".format(target) for target in build_info.get("global_targets", [])
        ]
        build_artifacts = (
            build_info.get("auto_location")
            if build_info.get("auto_location") is not None
            else "N/A"
        )

        print(
            "    Available directory targets ({}): {}".format(
                build_type.get_cmake_build_type(), " ".join(local_targets)
            )
        )
        print(
            "    Available deployment targets ({}): {}".format(
                build_type.get_cmake_build_type(), " ".join(global_targets)
            )
        )
        print("  ----------------------------------------------------------")
        print(
            "    Build artifact directory ({}): {}".format(
                build_type.get_cmake_build_type(), build_artifacts
            )
        )
        print()


def utility_entry(args):
    """ Main interface to F prime utility """
    parsed, cmake_args, make_args, parser = parse_args(args)
    deployment = Build.find_nearest_deployment(Path(parsed.path))

    try:
        if parsed.command is None:
            print("[ERROR] Must supply subcommand for fprime-util. See below.")
            parser.print_help(parsed, deployment)
            print_info(parsed, deployment)
        elif parsed.command == "info":
            print_info(parsed, deployment)
        elif parsed.command == "hash-to-file":
            lines = get_build(
                parsed, deployment, verbose=parsed.verbose
            ).find_hashed_file(parsed.hash)
            # Print out lines when found
            if lines:
                print("[INFO] File(s) associated with hash 0x{:x}".format(parsed.hash))
                for line in lines:
                    print("   ", line, end="")
            # Report nothing
            else:
                print(
                    "[ERROR] No file hashes found in {} build.{}".format(
                        "unittest" if parsed.unittest else "regular",
                        "" if parsed.unittest else " Do you need the --unittest flag?",
                    )
                )
        elif parsed.command == "generate" or parsed.command == "purge":
            for build_type in BuildType:
                build = Build(build_type, deployment, verbose=parsed.verbose)
                if parsed.command == "generate":
                    build.invent(parsed.platform, parsed.build_dir)
                    print(
                        "[INFO] {} build directory at: {}".format(
                            parsed.command.title(), build.build_dir
                        )
                    )
                    toolchain = find_toolchain(parsed.platform, parsed.path, build)
                    print(
                        "[INFO] Using toolchain file {} for platform {}".format(
                            toolchain, parsed.platform
                        )
                    )
                    if toolchain is not None:
                        cmake_args.update({"CMAKE_TOOLCHAIN_FILE": toolchain})
                    build.generate(cmake_args)
                else:
                    build.load(parsed.platform, parsed.build_dir)
                    print(
                        "[INFO] {} build directory at: {}".format(
                            parsed.command.title(), build.build_dir
                        )
                    )
                    if confirm() or parsed.force:
                        build.purge()
        else:
            target = get_target(parsed)
            build = get_build(parsed, deployment, parsed.verbose, target)
            build.load(parsed.platform, parsed.build_dir)
            build.execute(target, context=Path(parsed.path), make_args=make_args)
    except FprimeException as exc:
        print("[ERROR] {}".format(exc), file=sys.stderr)
        return 1
    return 0
