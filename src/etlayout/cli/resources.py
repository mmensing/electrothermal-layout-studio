# SPDX-License-Identifier: MPL-2.0
"""Deliberate noninteractive resource grants, scoped to one CLI operation."""

from etlayout.io.resources import ExternalResourcePolicy


def resource_arguments(parser):
    parser.add_argument(
        "--allow-outside-root",
        action="store_true",
        help="Authorize external layouts outside each selected project directory",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Separately authorize UNC/mapped-network layouts; does not imply outside-root approval",
    )


def policy_from_args(args):
    return ExternalResourcePolicy(
        allow_outside_root=getattr(args, "allow_outside_root", False),
        allow_network=getattr(args, "allow_network", False),
    )
