# SPDX-License-Identifier: MPL-2.0
"""Metadata-only inspection and explicit filesystem trust for project resources.

Inspection reads only the user-selected project container, never a resource named
inside it. Authorization is local to this opening operation, not a saved grant.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import zipfile
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath

from etlayout.io.identity import restore_document_id
from etlayout.models import Project, SimulationError

LAYOUT_SUFFIXES = {".gds", ".gdsii", ".oas", ".oasis"}


@dataclass(frozen=True)
class ExternalResourcePolicy:
    allow_outside_root: bool = False
    allow_network: bool = False
    approved_resources: frozenset[str] = frozenset()
    allowed_root: Path | None = None


@dataclass(frozen=True)
class ResourceDeclaration:
    key: str
    kind: str
    location: str
    category: str
    expected_sha256: str = ""
    status: str = "Not accessed; authorization/boundary check pending"


class ResourceAccessError(SimulationError):
    def __init__(self, reason: str, resource: ResourceDeclaration | None = None):
        self.reason, self.resource = reason, resource
        explanations = {
            "outside-root": "An external layout requires explicit outside-root authorization. Review project resources or use --allow-outside-root.",
            "network": "A potentially network-backed layout requires separate network opt-in. Review resources or use --allow-network; outside-root approval alone is insufficient.",
            "missing": "A declared layout is missing or inaccessible. Relink it in the resource review.",
            "changed": "Layout content changed since save; explicitly relink/reimport it before calculation.",
            "format": "The declared resource is not a supported local GDSII/OASIS layout.",
            "unresolved": "Project resources remain unresolved. Review resources or relink the layout before preparation, calculation or portable saving.",
            "links": "The resource contains unsupported or cyclic filesystem links. Relink to an ordinary approved layout file.",
            "size": "The external layout exceeds the project resource byte budget.",
        }
        super().__init__(
            explanations.get(reason, "Project resource resolution failed. Review or relink the resource.")
        )


def is_network_path(value: str) -> bool:
    text = value.replace("\\", "/").casefold()
    if text.startswith(("//?/unc/", "/??/unc/", "//./", "//?/globalroot/")):
        return True
    return text.startswith("//") and not re.match(r"^//\?/[a-z]:/", text)


def _spelling(value: str) -> str:
    if "\x00" in value or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        raise ResourceAccessError("format")
    if value.startswith("\\\\?\\") and not is_network_path(value):
        value = value[4:]
    return value


def display_location(value: str) -> str:
    """Resource review is explicit; credentials/control characters still stay hidden."""
    value = re.sub(r"(?i)(?:(?<=//)|(?<=\\\\))[^/\\@\s]+:[^/\\@\s]+@", "[credentials omitted]@", value)
    return "".join(char if char >= " " else " " for char in value)


def _lexical_path(value: str, root: Path) -> Path:
    value = _spelling(value)
    # Foreign Windows spellings are never interpreted as POSIX relative paths.
    if os.name != "nt" and (PureWindowsPath(value).drive or is_network_path(value)):
        raise ResourceAccessError("network" if is_network_path(value) else "format")
    return Path(os.path.abspath(os.path.join(root, value)))


def _network_drive(path: Path) -> bool:
    if os.name != "nt" or not path.drive:
        return False
    import ctypes

    # Queries the locally configured drive type; no resource content is opened.
    return ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4


def _contained(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _canonical_checked(path: Path, *, network: bool, root: Path | None = None, outside: bool = True) -> Path:
    """Inspect link targets before following them, including Windows junctions.

    Resolve approved local ancestors one component at a time. In particular,
    Path.resolve must not contact a UNC target before the separate network gate.
    """
    pending = deque(path.parts[1:])
    current = Path(path.anchor)
    links = 0
    while pending:
        part = pending.popleft()
        if part in ("", "."):
            continue
        candidate = current.parent if part == ".." else current / part
        if is_network_path(str(candidate)) or _network_drive(candidate):
            if not network:
                raise ResourceAccessError("network")
        # Ancestors of root are needed only to resolve the explicitly selected
        # project directory. Resource traversal starts at its canonical root.
        if (
            root is not None
            and not outside
            and not (_contained(candidate, root) or root.is_relative_to(candidate))
        ):
            raise ResourceAccessError("outside-root")
        try:
            info = os.lstat(candidate)
        except OSError:
            raise ResourceAccessError("missing") from None
        reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(info.st_mode) or reparse:
            links += 1
            if links > 40:
                raise ResourceAccessError("links")
            try:
                target = _spelling(os.readlink(candidate))
            except OSError:
                raise ResourceAccessError("links") from None
            if is_network_path(target) and not network:
                raise ResourceAccessError("network")
            target_path = _lexical_path(target, candidate.parent)
            if root is not None and not outside and not _contained(target_path, root):
                raise ResourceAccessError("outside-root")
            pending = deque([*target_path.parts[1:], *pending])
            current = Path(target_path.anchor)
        else:
            current = candidate
    return current


def classify_resource(
    location: str, source: Path, *, embedded: bool = False, key: str = "layout", expected_sha256: str = ""
) -> ResourceDeclaration:
    if embedded:
        category = "embedded"
    elif is_network_path(location):
        category = "network"
    else:
        try:
            candidate = _lexical_path(location, Path(os.path.abspath(source.parent)))
            category = (
                "inside-root"
                if _contained(candidate, Path(os.path.abspath(source.parent)))
                else "outside-root"
            )
        except ResourceAccessError:
            category = "unsupported"
    return ResourceDeclaration(
        key,
        "layout",
        location,
        category,
        expected_sha256,
        "Embedded in selected archive" if embedded else "Not accessed; authorization/boundary check pending",
    )


@dataclass
class ProjectInspection:
    source: Path
    project: Project
    resources: tuple[ResourceDeclaration, ...]
    archive_data: bytes | None = field(default=None, repr=False)
    metadata: dict = field(default_factory=dict, repr=False)
    relinked: bool = False
    discard_stale_result: bool = False

    def for_project(self, project: Project) -> ProjectInspection:
        """Review current persisted edits against the already inspected container.

        Runtime resource caches are intentionally excluded. History declarations
        and exact archive bytes still describe their original recorded runs.
        """
        from etlayout.io.project import physics_hash, project_from_dict

        current = project_from_dict(project.to_dict())
        if hasattr(project, "_document_id"):
            current._document_id = project._document_id
        metadata = deepcopy(self.metadata)
        if self.archive_data is not None or "project" in metadata or hasattr(current, "_document_id"):
            metadata["project"] = current.to_dict()
            if hasattr(current, "_document_id"):
                metadata["document_id"] = current._document_id
        else:
            metadata = current.to_dict()
        if current.layout.path != self.project.layout.path:
            metadata["embedded_layout"] = None
        embedded = metadata.get("embedded_layout")
        resources = tuple(item for item in self.resources if item.key != "layout")
        if embedded or current.layout.path:
            resource = classify_resource(
                embedded or current.layout.path, self.source, embedded=bool(embedded),
                expected_sha256=current.layout.sha256,
            )
            resources = (resource, *resources)
        changed = physics_hash(current, resolve_layout=False) != physics_hash(self.project, resolve_layout=False)
        return ProjectInspection(
            self.source, current, resources, self.archive_data, metadata, self.relinked,
            self.discard_stale_result or changed,
        )

    def unresolved_project(self) -> Project:
        project = deepcopy(self.project)
        project._resource_unresolved = True
        project._resource_inspection = self
        return project

    def relink(self, location: str) -> ProjectInspection:
        project = deepcopy(self.project)
        project.layout.path, project.layout.sha256 = location, ""
        metadata = deepcopy(self.metadata)
        metadata["embedded_layout"] = None
        return ProjectInspection(
            self.source,
            project,
            (classify_resource(location, self.source),),
            self.archive_data,
            metadata,
            True,
        )

    def summary(self) -> dict:
        return {
            "name": self.project.name,
            "schema_version": self.project.schema_version,
            "resources": [
                {
                    "key": item.key,
                    "type": item.kind,
                    "location": display_location(item.location),
                    "category": item.category,
                    "status": item.status,
                }
                for item in self.resources
            ],
            "metadata_only": True,
        }


def _read_container(stream, limit: int) -> bytes:
    """Bound every allocation as well as the aggregate selected-container size."""
    chunks = []
    remaining = limit + 1
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def inspect_project(
    path: str | Path,
    *,
    _archive_data: bytes | None = None,
    _prefix: str = "",
    allow_network_container: bool = False,
) -> ProjectInspection:
    """Read bounded container metadata without stat/hash/geometry of references."""
    from etlayout.io.project import (
        MAX_ARCHIVE_BYTES,
        MAX_METADATA_BYTES,
        inspect_archive_size,
        project_from_dict,
    )

    source = Path(path)
    if _archive_data is None:
        source = _canonical_checked(Path(os.path.abspath(source)), network=allow_network_container)
        limit = MAX_METADATA_BYTES if source.suffix.lower() == ".json" else MAX_ARCHIVE_BYTES
        try:
            with source.open("rb") as stream:
                raw = _read_container(stream, limit)
        except OSError as exc:
            raise SimulationError("Cannot read the selected project container") from exc
        if len(raw) > limit:
            raise SimulationError("Project container exceeds its byte budget")
    else:
        raw = _archive_data
    try:
        if source.suffix.lower() == ".json" and _archive_data is None:
            metadata = json.loads(raw)
            project = project_from_dict(
                metadata.get("project", metadata) if isinstance(metadata, dict) else metadata
            )
            restore_document_id(project, metadata.get("document_id") if isinstance(metadata, dict) and "project" in metadata else None, source)
            if project.layout.sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", project.layout.sha256):
                raise SimulationError("Invalid declared layout SHA-256 identity")
            resources = (
                ()
                if not project.layout.path
                else (
                    classify_resource(
                        project.layout.path,
                        source,
                        key=_prefix + "layout",
                        expected_sha256=project.layout.sha256,
                    ),
                )
            )
            inspection = ProjectInspection(source, project, resources, metadata=metadata)
            inspection._source_sha256 = hashlib.sha256(raw).hexdigest()
            return inspection
        inspect_archive_size(source, _archive_data=raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            metadata = json.loads(archive.read("project.json"))
            if isinstance(metadata, dict) and metadata.get("schema_version") == 3:
                from etlayout.io.archive_v3 import ArchiveV3Reader
                with ArchiveV3Reader(raw, source_path=source) as reader:
                    project = deepcopy(reader.project)
                    resources = []
                    embedded = metadata.get("embedded_layout")
                    if embedded or project.layout.path:
                        resources.append(classify_resource(embedded or project.layout.path, source, embedded=bool(embedded),
                            key=_prefix + "layout", expected_sha256=project.layout.sha256))
                    for record in reader.records:
                        if not record["available"]:
                            continue  # Unavailable rows confer no resource authority.
                        descriptor = reader._descriptor(record["id"])
                        resource_key = reader.layout_resource_key(record["id"])
                        if resource_key == "layout":
                            continue  # The identical current source is already declared.
                        setup = descriptor["project"]["layout"]
                        embedded_ref = descriptor["layout"]
                        location = reader.object_name(embedded_ref["$ref"]) if embedded_ref else setup["path"]
                        if location:
                            resources.append(classify_resource(location, source, embedded=bool(embedded_ref),
                                key=_prefix + resource_key, expected_sha256=setup["sha256"]))
                    return ProjectInspection(source, project, tuple(resources), raw, metadata)
            if (
                not isinstance(metadata, dict)
                or metadata.get("format") != "etlayout"
                or type(metadata.get("schema_version")) is not int
                or metadata["schema_version"] not in (1, 2)
            ):
                raise SimulationError("Unsupported project format/version")
            project = project_from_dict(metadata["project"])
            restore_document_id(project, metadata.get("document_id"), source)
            if project.layout.sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", project.layout.sha256):
                raise SimulationError("Invalid declared layout SHA-256 identity")
            embedded = metadata.get("embedded_layout")
            if embedded is not None and not isinstance(embedded, str):
                raise SimulationError("Invalid embedded layout declaration")
            resources = []
            if embedded or project.layout.path:
                resources.append(
                    classify_resource(
                        embedded or project.layout.path,
                        source,
                        embedded=bool(embedded),
                        key=_prefix + "layout",
                        expected_sha256=project.layout.sha256,
                    )
                )
            for name in sorted(archive.namelist()):
                if name.startswith("runs/run_") and name.endswith(".etl"):
                    child = inspect_project(
                        source, _archive_data=archive.read(name), _prefix=_prefix + name + ":"
                    )
                    resources.extend(child.resources)
            return ProjectInspection(source, project, tuple(resources), raw, metadata)
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        raise SimulationError("Invalid project metadata or resource declarations") from exc


def resolve_layout(inspection: ProjectInspection, policy: ExternalResourcePolicy) -> tuple[Path, str]:
    from etlayout.io.project import MAX_ARCHIVE_BYTES, file_hash

    resource = next(
        item for item in inspection.resources if item.key.endswith("layout") and item.category != "embedded"
    )
    try:
        raw = _spelling(resource.location)
        if is_network_path(raw) and not policy.allow_network:
            raise ResourceAccessError("network")
        outside = policy.allow_outside_root or resource.key in policy.approved_resources
        project_root = Path(os.path.abspath(inspection.source.parent))
        authorized_root = Path(os.path.abspath(policy.allowed_root)) if policy.allowed_root else project_root
        candidate = _lexical_path(raw, project_root)
        if not _contained(candidate, authorized_root) and not outside:
            raise ResourceAccessError("outside-root")
        root = _canonical_checked(authorized_root, network=policy.allow_network)
        if not _contained(candidate, root) and not outside:
            raise ResourceAccessError("outside-root")
        if candidate.suffix.lower() not in LAYOUT_SUFFIXES:
            raise ResourceAccessError("format")
        candidate = _canonical_checked(candidate, network=policy.allow_network, root=root, outside=outside)
        if not candidate.is_file():
            raise ResourceAccessError("missing")
        if candidate.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ResourceAccessError("size")
        digest = file_hash(candidate)
        if resource.expected_sha256 and digest != resource.expected_sha256:
            raise ResourceAccessError("changed")
        return candidate, digest
    except ResourceAccessError as exc:
        if exc.reason in {"outside-root", "network"}:
            resource = replace(resource, category=exc.reason)
        raise ResourceAccessError(exc.reason, resource) from None
    except OSError:
        raise ResourceAccessError("missing", resource) from None


def require_resources(project: Project) -> None:
    if getattr(project, "_resource_unresolved", False):
        raise ResourceAccessError("unresolved")
