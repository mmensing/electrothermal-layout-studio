# SPDX-License-Identifier: MPL-2.0
"""Launch the actual desktop smoke entry; --offscreen selects CI rendering."""

if __name__ == "__main__":
    import multiprocessing
    import os
    import sys

    multiprocessing.freeze_support()
    arguments = sys.argv[1:]
    if "--offscreen" in arguments:
        arguments.remove("--offscreen")
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from etlayout.app.main import main

    raise SystemExit(main([*arguments, "--smoke-test", "--no-recovery"]))
