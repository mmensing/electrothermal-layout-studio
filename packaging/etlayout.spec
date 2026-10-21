# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: MPL-2.0
"""Native Windows onedir distribution. Build through scripts/build_windows.py."""
from pathlib import Path
import hashlib
import json
import sys
import runpy
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
resource_policy = runpy.run_path(str(root / "packaging" / "resources.py"))
resource_files = resource_policy["resource_files"]
datas = collect_data_files("etlayout") + copy_metadata("etlayout")
# Preserve the independently hash-bound modified Qt runtime receipt.
datas += collect_data_files("shiboken6", includes=["ETLAYOUT_RUNTIME_BUILD.json"])
# Experiment identities include exact numerical dependencies, including when
# importlib.metadata executes inside the frozen optimizer.
for dependency in ("numpy", "scipy", "shapely", "gmsh", "klayout", "PySide6", "shiboken6",
                   "pyqtgraph", "matplotlib", "meshio", "platformdirs", "threadpoolctl", "psutil"):
    datas += copy_metadata(dependency)
package = root / "src" / "etlayout"
source_hashes = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(package.rglob("*.py"))}
implementation = root / "build" / "frozen-identity" / "implementation.json"
implementation.parent.mkdir(parents=True, exist_ok=True)
implementation.write_text(json.dumps({"schema_version": 1,
    "version": runpy.run_path(str(package / "__init__.py"))["__version__"],
    "source_sha256": hashlib.sha256(json.dumps(
    source_hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}), encoding="utf-8")
datas.append((str(implementation), "etlayout/data"))
binaries = collect_dynamic_libs("klayout")
hiddenimports = collect_submodules("etlayout") + collect_submodules("klayout.db_plugins")
hiddenimports += ["klayout.dbcore", "klayout.tlcore", "PySide6.QtSvg", "PySide6.QtSvgWidgets"]
hiddenimports += ["matplotlib.backends.backend_svg", "matplotlib.backends.backend_pdf"]

# gmsh's wheel puts its DLL beside site-packages, outside the import package.
# At runtime gmsh.py looks beside its own __file__, i.e. bundle/_internal.
gmsh_override = os.environ.get("ETLAYOUT_GMSH_LIBRARY")
gmsh_candidates = ([Path(gmsh_override)] if gmsh_override else
                   list((Path(sys.prefix) / "Lib").glob("gmsh-*.dll")))
if not gmsh_candidates or any(not path.is_file() for path in gmsh_candidates):
    raise RuntimeError("Gmsh wheel DLL missing under the build environment's Lib directory")
binaries += [(str(path), ".") for path in gmsh_candidates]

for directory in ("examples", "docs"):
    for path in resource_files(root, directory):
        datas.append((str(path), path.relative_to(root).parent.as_posix()))
if (root / "artifacts" / "licenses").is_dir():
    datas.append((str(root / "artifacts" / "licenses"), "licenses"))
if (root / "packaging" / "license-texts").is_dir():
    datas.append((str(root / "packaging" / "license-texts"), "licenses/license-texts"))
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    datas.append((str(python_license), "licenses/Python"))
for filename in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json", "REUSE.toml", "requirements-windows-py312.lock"):
    path = root / filename
    if path.exists():
        datas.append((str(path), "."))

a = Analysis(
    [str(root / "scripts" / "frozen_entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtPdf",
              "PySide6.QtPdfWidgets", "PySide6.QtMultimedia", "tkinter", "pytest", "IPython", "jupyter"],
    noarchive=False,
)
# QtGui hooks discover optional PDF/virtual-keyboard plugins and then their QML
# dependencies. The desktop does not use these plugins. Filter both the plugin
# and its unused libraries; verify the actual candidate with the SBOM and smoke tests.
keep_qt_entry = runpy.run_path(str(root / "packaging" / "qt_contents.py"))["keep_qt_entry"]
a.binaries = [entry for entry in a.binaries if keep_qt_entry(entry[0])]
# pip's PEP 610 origin is installer provenance, potentially a private local URL.
# Retain METADATA/WHEEL/RECORD and license files used for identity and attribution.
a.datas = [entry for entry in a.datas if keep_qt_entry(entry[0])
           and resource_policy["distributable_metadata"](entry[0])]
publication_assets = runpy.run_path(str(root / "packaging" / "publication_assets.py"))
a.datas, excluded_data = publication_assets["filter_publication_data"](a.datas)
a.datas, modified_data = publication_assets["prepare_publication_assets"](
    a.datas, root / "build" / "publication-assets")
asset_manifest = root / "build" / "publication-assets" / "PUBLICATION_ASSETS.json"
asset_manifest.write_text(json.dumps({"schema_version": 1, "excluded": excluded_data,
                                      "modified": modified_data}, indent=2) + "\n", encoding="utf-8")
a.datas.append(("PUBLICATION_ASSETS.json", str(asset_manifest), "DATA"))
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ElectrothermalLayoutStudio",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="etlayout",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="ElectrothermalLayoutStudio")
