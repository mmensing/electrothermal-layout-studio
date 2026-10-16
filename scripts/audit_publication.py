# SPDX-License-Identifier: MPL-2.0
"""Inspect source trees and distribution archives before sharing them.

This is a conservative local check, not a proof of provenance, license compliance,
or absence of every secret. Review legitimate findings privately. Do not upload
findings containing sensitive material to a public issue or CI artifact.
"""
from __future__ import annotations

import argparse
import base64
import bz2
import codecs
import hashlib
import io
import json
import lzma
import re
import runpy
import struct
import tarfile
import tempfile
import warnings
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_ITEM_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_DEPTH = 3
MAX_LAYOUT_ITEMS = 100_000
MAX_IMAGE_PIXELS = 40_000_000
_SOURCE_GENERATED = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache',
                     '.mypy_cache', '.artifacts', 'artifacts', 'build', 'dist'}
_PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'cloud_access_key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'secret_assignment': re.compile(r'''(?i)\b(?:password|passwd|api_key|access_token|client_secret|auth_token)\s*[:=]\s*["']([^"'\s]{12,})["']'''),
    'authenticated_url': re.compile(r'https?://[^\s/:@]+:[^\s/@]+@[^\s]+'),
    'personal_path': re.compile(r'(?i)(?:[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]+|/(?:home|Users)/)[^\s"\x27<>]+'),
    'email': re.compile(r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
    'private_address': re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'),
}


def _fingerprint(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()[:16]


def _notice_path(name: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(name.replace('!', '/')).parts]
    return any(part in {'licenses', 'license-texts', 'third_party_notices.md'} or
               re.fullmatch(r'(?:license|licence|copying|notice|copyright|authors)(?:[._-].*)?', part)
               for part in parts)


def _public_email(value: str) -> bool:
    domain = value.rsplit('@', 1)[-1].lower()
    return domain == 'users.noreply.github.com' or domain in {'example.com', 'example.net', 'example.org'} or domain.endswith(('.invalid', '.example', '.test'))


def _safe_name(name: str) -> str:
    # A secret can occur in an archive member's filename too. Never echo it.
    if any(pattern.search(name) for pattern in _PATTERNS.values()):
        return '[redacted-path:' + _fingerprint(name) + ']'
    return name


class PublicationAudit:
    def __init__(self, *, forbidden_text=(), allowed_emails=()):
        self.forbidden_text = tuple(value.casefold() for value in forbidden_text if value)
        self.allowed_emails = {value.casefold() for value in allowed_emails}
        self.findings: list[dict] = []
        self.counts = Counter()
        self._archive_bytes = 0
        self._seen = set()

    def finding(self, name: str, kind: str, value: str | bytes = '', *, line: int | None = None):
        key = (name, kind, _fingerprint(value), line)
        if key in self._seen:
            return
        self._seen.add(key)
        safe = '[redacted-path:' + _fingerprint(name) + ']' if any(value in name.casefold() for value in self.forbidden_text) else _safe_name(name)
        item = dict(path=safe, kind=kind, fingerprint=key[2])
        if line is not None:
            item['line'] = line
        self.findings.append(item)

    def text(self, name: str, value: str, *, line_offset: int = 0):
        for kind, pattern in _PATTERNS.items():
            line_cursor, line_number = 0, line_offset + 1
            for match in pattern.finditer(value):
                matched = match.group(0)
                if kind == 'email':
                    # Git's SSH transport uses a service account and a colon
                    # before the repository path; this is not a personal email.
                    ssh_origin = matched.casefold().startswith('git@') and value[match.end():].startswith(':')
                    if ssh_origin or _notice_path(name) or _public_email(matched) or matched.casefold() in self.allowed_emails:
                        continue
                if kind == 'private_address' and any(int(part) > 255 for part in matched.split('.')):
                    continue
                # Matches are ordered. Count each intervening newline once per
                # pattern instead of rescanning a huge prefix for every finding.
                line_number += value.count('\n', line_cursor, match.start())
                line_cursor = match.start()
                self.finding(name, kind, matched, line=line_number)
        folded = value.casefold()
        for forbidden in self.forbidden_text:
            if forbidden in folded:
                self.finding(name, 'forbidden_text', forbidden)

    def json_strings(self, name: str, value, depth: int = 0):
        if depth > 100:
            self.finding(name, 'metadata_nesting_budget')
            return
        if isinstance(value, dict):
            for key, child in value.items():
                self.text(name + '!json-key', str(key))
                self.json_strings(name, child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                self.json_strings(name, child, depth + 1)
        elif isinstance(value, str):
            self.text(name + '!json-string', value)

    def image(self, name: str, data: bytes):
        from PIL import Image
        self.counts['images'] += 1
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.width * picture.height > MAX_IMAGE_PIXELS:
                        self.finding(name, 'image_pixel_budget')
                        return
                    for key, value in {**picture.info, **dict(picture.getexif())}.items():
                        self.text(name + '!image-metadata', str(key))
                        if isinstance(value, bytes):
                            self.text(name + '!image-metadata', value.decode('utf8', 'replace'))
                        else:
                            self.text(name + '!image-metadata', str(value))
                with Image.open(io.BytesIO(data)) as verified:
                    verified.verify()
        except Exception as exc:
            self.finding(name, 'image_read_error', type(exc).__name__)

    def svg(self, name: str, data: bytes, depth: int):
        # ElementTree does not fetch the normal external SVG DTD. Reject entity
        # declarations and never execute scripts or follow external image links.
        if b'<!ENTITY' in data.upper():
            self.finding(name, 'svg_entity_declaration')
            return
        try:
            document = ET.fromstring(data)
            for element in document.iter():
                for key, value in element.attrib.items():
                    if key.rsplit('}', 1)[-1] != 'href' or not value.startswith('data:image/'):
                        continue
                    header, separator, encoded = value.partition(',')
                    if separator and header.endswith(';base64'):
                        if len(encoded) > MAX_ITEM_BYTES * 4 // 3 + 4:
                            self.finding(name, 'embedded_image_budget')
                            continue
                        image_data = base64.b64decode(''.join(encoded.split()), validate=True)
                        if depth >= MAX_DEPTH:
                            self.finding(name, 'nesting_budget')
                        else:
                            self.image(name + '!embedded-image', image_data)
        except (ValueError, ET.ParseError) as exc:
            self.finding(name, 'svg_read_error', type(exc).__name__)

    def gds(self, name: str, data: bytes):
        self.counts['layouts'] += 1
        position = 0
        while position < len(data):
            if position + 4 > len(data):
                self.finding(name, 'gds_truncated_record')
                return
            size, _record, datatype = struct.unpack('>HBB', data[position:position + 4])
            if size < 4 or size % 2 or position + size > len(data):
                self.finding(name, 'gds_invalid_record')
                return
            if datatype == 6:
                self.text(name + '!layout-string', data[position + 4:position + size].rstrip(b'\0').decode('ascii', 'replace'))
            position += size

    def oasis(self, name: str, data: bytes):
        self.counts['layouts'] += 1
        try:
            import klayout.db as kdb
        except ImportError:
            self.finding(name, 'oasis_inspector_unavailable')
            return
        try:
            with tempfile.TemporaryDirectory(prefix='layout-audit-') as directory:
                path = Path(directory) / 'input.oas'
                path.write_bytes(data)
                layout = kdb.Layout()
                layout.read(str(path))
                items = 0

                def properties(values):
                    self.json_strings(name + '!layout-property', {str(k): str(v) for k, v in values.items()})

                properties(layout.properties())
                for cell in layout.each_cell():
                    items += 1
                    if items > MAX_LAYOUT_ITEMS:
                        self.finding(name, 'layout_item_budget')
                        return
                    self.text(name + '!cell-name', cell.name)
                    properties(cell.properties())
                    for instance in cell.each_inst():
                        items += 1
                        properties(instance.properties())
                        if items > MAX_LAYOUT_ITEMS:
                            self.finding(name, 'layout_item_budget')
                            return
                    for layer in layout.layer_indexes():
                        for shape in cell.shapes(layer).each():
                            items += 1
                            properties(shape.properties())
                            if shape.is_text():
                                self.text(name + '!layout-text', shape.text.string)
                            if items > MAX_LAYOUT_ITEMS:
                                self.finding(name, 'layout_item_budget')
                                return
        except Exception as exc:
            self.finding(name, 'oasis_read_error', type(exc).__name__)

    def archive(self, name: str, data: bytes, depth: int):
        if depth >= MAX_DEPTH:
            self.finding(name, 'nesting_budget')
            return
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ARCHIVE_MEMBERS:
                    self.finding(name, 'archive_member_budget')
                    return
                self.counts['archives'] += 1
                self.text(name + '!archive-comment', archive.comment.decode('utf8', 'replace'))
                for member in infos:
                    self.counts['archive_members'] += 1
                    member_name = name + '!' + member.filename
                    self.text(member_name, member.filename)
                    path = PurePosixPath(member.filename.replace('\\', '/'))
                    if path.is_absolute() or '..' in path.parts or re.match(r'^[A-Za-z]:', member.filename):
                        self.finding(member_name, 'unsafe_archive_path')
                        continue
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        self.finding(member_name, 'unsupported_archive_entry')
                        continue
                    if member.flag_bits & 1:
                        self.finding(member_name, 'encrypted_archive_member')
                        continue
                    if member.is_dir():
                        continue
                    self._archive_bytes += member.file_size
                    if member.file_size > MAX_ITEM_BYTES or self._archive_bytes > MAX_ARCHIVE_BYTES:
                        self.finding(member_name, 'archive_size_budget')
                        continue
                    if member.compress_size and member.file_size > member.compress_size * 10_000:
                        self.finding(member_name, 'archive_ratio_budget')
                        continue
                    with archive.open(member) as stream:
                        content = stream.read(MAX_ITEM_BYTES + 1)
                    self.bytes(member_name, content, depth + 1)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            self.finding(name, 'archive_read_error', type(exc).__name__)

    def tar(self, name: str, data: bytes, depth: int):
        if depth >= MAX_DEPTH:
            self.finding(name, 'nesting_budget')
            return
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
                self.counts['archives'] += 1
                for index, member in enumerate(archive):
                    if index >= MAX_ARCHIVE_MEMBERS:
                        self.finding(name, 'archive_member_budget')
                        return
                    self.counts['archive_members'] += 1
                    member_name = name + '!' + member.name
                    self.text(member_name, member.name)
                    path = PurePosixPath(member.name.replace('\\', '/'))
                    if path.is_absolute() or '..' in path.parts or re.match(r'^[A-Za-z]:', member.name):
                        self.finding(member_name, 'unsafe_archive_path')
                        continue
                    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                        self.finding(member_name, 'unsupported_archive_entry')
                        continue
                    if member.uname not in ('', 'root') or member.gname not in ('', 'root'):
                        self.finding(member_name, 'archive_owner_metadata')
                    self.json_strings(member_name + '!pax-metadata', member.pax_headers)
                    if member.isdir():
                        continue
                    self._archive_bytes += member.size
                    if member.size > MAX_ITEM_BYTES or self._archive_bytes > MAX_ARCHIVE_BYTES:
                        self.finding(member_name, 'archive_size_budget')
                        continue
                    stream = archive.extractfile(member)
                    if stream is None:
                        self.finding(member_name, 'archive_read_error')
                        continue
                    with stream:
                        self.bytes(member_name, stream.read(MAX_ITEM_BYTES + 1), depth + 1)
        except (OSError, ValueError, tarfile.TarError) as exc:
            self.finding(name, 'archive_read_error', type(exc).__name__)

    def bytes(self, name: str, data: bytes, depth: int = 0):
        self.counts['items'] += 1
        self.counts['inspected_bytes'] += len(data)
        if len(data) > MAX_ITEM_BYTES:
            self.finding(name, 'item_size_budget')
            return
        self.text(name + '!filename', name)
        if zipfile.is_zipfile(io.BytesIO(data)):
            self.archive(name, data, depth)
            return
        suffix = Path(name).suffix.lower()
        if name.lower().endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz')):
            self.tar(name, data, depth)
            return
        if suffix in {'.etl', '.npz', '.zip', '.whl'}:
            self.finding(name, 'archive_read_error')
            return
        if suffix in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.tif', '.tiff', '.bmp', '.ico'}:
            self.image(name, data)
            return
        if suffix == '.gds':
            self.gds(name, data)
            return
        if suffix == '.oas':
            self.oasis(name, data)
            return
        if suffix == '.json':
            try:
                self.json_strings(name, json.loads(data.decode('utf8')))
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                self.finding(name, 'json_read_error', type(exc).__name__)
        if suffix == '.svg':
            self.svg(name, data, depth)
        if b'\0' in data[:4096] or suffix in {'.npy', '.pyc', '.bin'}:
            # Inspect printable strings without deserializing code or pickles.
            self.text(name + '!binary-string', '\n'.join(value.decode('ascii') for value in re.findall(rb'[\x20-\x7e]{6,}', data)))
        else:
            self.text(name, data.decode('utf8', 'replace'))

    def tree(self, root: Path, *, source: bool = False, output: Path | None = None):
        if root.is_symlink():
            self.finding(root.name, 'symlink_input')
            return
        root = root.resolve()
        candidates = [root] if root.is_file() else sorted(root.rglob('*'))
        for path in candidates:
            relative = path.name if root.is_file() else path.relative_to(root).as_posix()
            parts = PurePosixPath(relative).parts
            if not root.is_file() and parts and parts[0] == '.git':
                continue
            if output is not None and path.resolve() == output.resolve():
                continue
            if source and any(part in _SOURCE_GENERATED or part.endswith('.egg-info') for part in parts):
                if path.is_file():
                    self.counts['generated_files_excluded'] += 1
                continue
            if path.is_symlink():
                self.finding(relative, 'symlink_input')
                continue
            if not path.is_file():
                continue
            self.counts['files'] += 1
            if self.counts['files'] > MAX_ARCHIVE_MEMBERS:
                self.finding(relative, 'tree_file_budget')
                break
            try:
                if path.stat().st_size > MAX_ITEM_BYTES:
                    self.finding(relative, 'item_size_budget')
                    continue
                self.bytes(relative, path.read_bytes())
            except OSError as exc:
                self.finding(relative, 'file_read_error', type(exc).__name__)

    def report(self, *, source: bool = False):
        return dict(schema_version=1, mode='source' if source else 'complete', clean=not self.findings,
                    counts=dict(self.counts), findings=self.findings,
                    limitations='Pattern and bounded-format checks do not establish rights, legal compliance, or absence of every secret. Required upstream license/copyright email notices and documented example addresses are retained. Review code provenance and visible image content manually.')

@dataclass(frozen=True)
class ArtifactLimits:
    """Explicit release-only bounds; ordinary source inspection stays unchanged."""
    compressed_bytes: int = 2 * 1024**3
    expanded_bytes: int = 8 * 1024**3
    member_bytes: int = 512 * 1024**2
    members: int = 200_000
    depth: int = 5
    directory_bytes: int = 64 * 1024**2
    metadata_bytes: int = 1024**2
    ratio: int = 10_000
    chunk_bytes: int = 1024**2
    overlap_chars: int = 65_536
    findings: int = 2_000
    lzma_memory_bytes: int = 128 * 1024**2

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError('Artifact limits must be finite positive integers')
        if self.chunk_bytes > 1024**2 or self.metadata_bytes > MAX_ITEM_BYTES:
            raise ValueError('Artifact inspection memory window exceeds its ceiling')


class _ArtifactStop(ValueError):
    pass


class _ArtifactReader:
    """Bound every decompressed read, including TAR padding and metadata."""
    def __init__(self, stream, limit, chunk):
        self.stream, self.limit, self.chunk = stream, limit, chunk
        self.position = 0

    def read(self, count):
        if count < 0:
            raise ValueError('Unbounded artifact read')
        data = self.stream.read(min(count, self.chunk, self.limit - self.position + 1))
        self.position += len(data)
        if self.position > self.limit:
            raise ValueError('Decompressed artifact exceeds its byte/ratio budget')
        return data

    def exact(self, count):
        parts = []
        while count:
            part = self.read(count)
            if not part:
                raise ValueError('Truncated artifact payload')
            parts.append(part)
            count -= len(part)
        return b''.join(parts)

    def drain(self, count):
        while count:
            data = self.read(count)
            if not data:
                raise ValueError('Truncated artifact payload')
            count -= len(data)


class _ArtifactXZ:
    """XZ decompression with an explicit dictionary and output memory bound."""
    def __init__(self, stream, limits, reject_trailing=None):
        self.stream, self.limits = stream, limits
        self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=limits.lzma_memory_bytes)
        self.ended = False
        self.reject_trailing = reject_trailing

    def _trailing(self):
        if self.reject_trailing is not None:
            self.reject_trailing()
            raise _ArtifactStop('Use one XZ stream; concatenated/trailing data are unsupported')
        raise ValueError('Unexpected trailing XZ stream')

    def read(self, count):
        if count < 0 or count > self.limits.chunk_bytes:
            raise ValueError('Unbounded XZ read')
        result = bytearray()
        while len(result) < count and not self.ended:
            if self.decoder.eof:
                # XZ stream padding is zero; concatenated/hidden streams fail closed.
                if any(self.decoder.unused_data):
                    self._trailing()
                while trailing := self.stream.read(self.limits.chunk_bytes):
                    if any(trailing):
                        self._trailing()
                self.ended = True
                break
            data = self.stream.read(self.limits.chunk_bytes) if self.decoder.needs_input else b''
            if not data and self.decoder.needs_input:
                raise ValueError('Truncated XZ stream')
            result.extend(self.decoder.decompress(data, max_length=count - len(result)))
        return bytes(result)


class _ArtifactBZIP2:
    """One CRC-checked BZIP2 stream with finite output and no ignored tail."""
    def __init__(self, stream, limits, reject_trailing):
        self.stream, self.limits = stream, limits
        self.decoder = bz2.BZ2Decompressor()
        self.ended = False
        self.reject_trailing = reject_trailing

    def read(self, count):
        if count < 0 or count > self.limits.chunk_bytes:
            raise ValueError('Unbounded BZIP2 read')
        result = bytearray()
        while len(result) < count and not self.ended:
            if self.decoder.eof:
                if any(self.decoder.unused_data):
                    self.reject_trailing()
                    raise _ArtifactStop('Use one BZIP2 stream; concatenated/trailing data are unsupported')
                while trailing := self.stream.read(self.limits.chunk_bytes):
                    if any(trailing):
                        self.reject_trailing()
                        raise _ArtifactStop('Use one BZIP2 stream; concatenated/trailing data are unsupported')
                self.ended = True
                break
            data = self.stream.read(self.limits.chunk_bytes) if self.decoder.needs_input else b''
            if not data and self.decoder.needs_input:
                raise ValueError('Truncated BZIP2 stream')
            result.extend(self.decoder.decompress(data, max_length=count - len(result)))
        return bytes(result)

    def close(self):
        # The caller reuses the compressed seekable input for its second pass.
        pass


class _ArtifactGZIP:
    """One CRC-checked GZIP stream with bounded output and no hidden headers.

    zlib stops at the first stream, unlike gzip.GzipFile's transparent member
    concatenation. Zero transport padding is allowed; another stream or any
    other trailing bytes fail closed instead of hiding uninspected metadata.
    """
    def __init__(self, stream, limits, reject_trailing):
        self.stream, self.limits = stream, limits
        self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self.pending = b''
        self.ended = False
        self.reject_trailing = reject_trailing

    def _finish(self):
        if any(self.decoder.unused_data):
            self.reject_trailing()
            raise _ArtifactStop('Use one GZIP stream; concatenated streams/trailing data are unsupported')
        while trailing := self.stream.read(self.limits.chunk_bytes):
            if any(trailing):
                self.reject_trailing()
                raise _ArtifactStop('Use one GZIP stream; concatenated streams/trailing data are unsupported')
        self.ended = True

    def read(self, count):
        if count < 0 or count > self.limits.chunk_bytes:
            raise ValueError('Unbounded GZIP read')
        result = bytearray()
        while len(result) < count and not self.ended:
            if self.decoder.eof:
                self._finish()
                break
            data = self.pending or self.stream.read(self.limits.chunk_bytes)
            self.pending = b''
            block = self.decoder.decompress(data, max_length=count - len(result))
            self.pending = self.decoder.unconsumed_tail
            result.extend(block)
            if not data and not block and not self.decoder.eof:
                raise ValueError('Truncated GZIP stream')
        return bytes(result)

    def close(self):
        # The caller owns and reuses the seekable compressed source between passes.
        pass


class ArtifactAudit(PublicationAudit):
    """Stream explicitly selected release artifacts, never enabling publication."""
    KINDS = {'binary-candidate', 'third-party-source'}
    _STRUCTURED = {'.json', '.svg', '.gds', '.oas', '.oasis', '.png', '.jpg',
                   '.jpeg', '.webp', '.gif', '.tif', '.tiff', '.bmp', '.ico'}
    _NATIVE = {'.exe', '.dll', '.pyd', '.so', '.dylib', '.msi', '.msix', '.appx', '.lib', '.a'}
    _TEXT_SOURCE = {'.py', '.pyi', '.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.f',
                    '.f90', '.txt', '.rst', '.md', '.xml', '.in', '.cmake', '.toml',
                    '.yml', '.yaml'}

    def __init__(self, *, artifact_kind, limits=None, **kwargs):
        super().__init__(**kwargs)
        if artifact_kind not in self.KINDS:
            raise ValueError('Select an explicit supported release artifact kind')
        self.artifact_kind = artifact_kind
        # Large corresponding-source collections retain thousands of mandatory
        # upstream copyright/contact strings. Their private review is still exact
        # and finite; binary candidates and ordinary source policy keep old caps.
        self.limits = (limits if limits is not None else ArtifactLimits(findings=20_000)
                       if artifact_kind == 'third-party-source' else ArtifactLimits())
        self._logical_bytes = 0
        self._members = 0

    def finding(self, name, kind, value='', *, line=None):
        if len(self.findings) >= self.limits.findings:
            super().finding('artifact', 'finding_budget')
            raise _ArtifactStop('Artifact finding budget exceeded')
        super().finding(name, kind, value, line=line)

    def _reserve(self, name, size):
        self._members += 1
        self._logical_bytes += size
        if self._members > self.limits.members:
            self.finding(name, 'archive_member_budget')
            raise _ArtifactStop('Artifact member budget exceeded')
        if size > self.limits.member_bytes or self._logical_bytes > self.limits.expanded_bytes:
            self.finding(name, 'archive_size_budget')
            raise _ArtifactStop('Artifact expanded byte budget exceeded')

    def _name(self, name, seen, *, directory=False):
        normalized = name.replace('\\', '/')
        parts = normalized.rstrip('/').split('/')
        if (not normalized or normalized.startswith('/') or any(part.casefold() in {'', '.', '..', '.git'}
                or ':' in part or part.rstrip(' .') != part for part in parts)):
            self.finding(name, 'unsafe_archive_path')
            raise _ArtifactStop('Unsafe artifact member name')
        canonical = '/'.join(parts).casefold()
        existing = seen.get(canonical)
        ancestors = ['/'.join(parts[:index]).casefold() for index in range(1, len(parts))]
        if (existing in {'file', 'directory'} or (existing == 'implicit' and not directory)
                or any(seen.get(parent) == 'file' for parent in ancestors)):
            self.finding(name, 'duplicate_archive_member')
            raise _ArtifactStop('Ambiguous artifact member name')
        for parent in ancestors:
            seen.setdefault(parent, 'implicit')
        seen[canonical] = 'directory' if directory else 'file'

    def _stream_text(self, name, stream, size, prefix=b'', *, native=False, font=False):
        decoders = [codecs.getincrementaldecoder('utf8')('replace')]
        if native or font:
            decoders += [codecs.getincrementaldecoder(encoding)('replace')
                         for encoding in ('utf-16-le', 'utf-16-be')]
        tails = [''] * len(decoders)
        offsets = [0] * len(decoders)
        if any(len(value) > self.limits.overlap_chars // 2 for value in self.forbidden_text):
            self.finding(name, 'artifact_pattern_budget')
            return

        def scan(data, final=False):
            for index, decoder in enumerate(decoders):
                text = decoder.decode(data, final=final)
                if native or font:
                    text = re.sub(r'[^\x20-\x7e\n\r\t]', '\n', text)
                combined = tails[index] + text
                # Unbounded printable tokens could hide a regex terminator beyond
                # the overlap. Reject them instead of silently assuming a clean scan.
                if any(match.end() - match.start() > self.limits.overlap_chars
                   for match in re.finditer(r'[\x20-\x7e]+', combined)):
                    self.finding(name, 'artifact_text_window_budget')
                    raise _ArtifactStop('Artifact text exceeds its bounded scan window')
                self.text(name + ('!native-string' if native else '!font-string' if font else ''), combined,
                          line_offset=offsets[index])
                retained = combined[-self.limits.overlap_chars:]
                offsets[index] += combined[:-len(retained)].count('\n') if retained else combined.count('\n')
                tails[index] = retained

        remaining = size - len(prefix)
        scan(prefix)
        while remaining:
            data = stream.read(min(remaining, self.limits.chunk_bytes))
            if not data:
                raise ValueError('Truncated artifact member')
            scan(data)
            remaining -= len(data)
            self.counts['streamed_bytes'] += len(data)
        scan(b'', final=True)

    def _inspect(self, name, stream, size, depth):
        self.counts['items'] += 1
        self.text(name + '!filename', name)
        prefix = stream.read(min(size, 8))
        if len(prefix) != min(size, 8):
            raise ValueError('Truncated artifact member')
        suffix = Path(name).suffix.lower()
        leaf = PurePosixPath(name.replace('!', '/')).name.casefold()
        # COPYING.LIB is the conventional LGPL text, not an import library.
        # A directory called licenses cannot exempt an arbitrary native .lib;
        # only actual notice filenames qualify, and native magic always wins.
        library_notice = (suffix == '.lib' and _notice_path(name)
                          and re.fullmatch(r'(?:copying|license|licence)(?:[._-].*)?', leaf) is not None)
        # Qt's qmake build configuration uses the exact Info.plist.lib filename
        # for an XML property-list template. Validate the complete bounded XML
        # before accepting this source-data spelling of the native .lib suffix.
        plist_template = leaf == 'info.plist.lib' and prefix.startswith(b'<?xml')
        native = (suffix in self._NATIVE and not library_notice and not plist_template) or prefix.startswith(
            (b'MZ', b'\x7fELF', b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf',
             b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'!<arch>\n'))
        is_zip = prefix.startswith((b'PK\x03\x04', b'PK\x05\x06')) or suffix in {'.zip', '.whl', '.etl', '.npz'}
        is_tar = (name.lower().endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz'))
                  or prefix.startswith((b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00')))
        if depth == 0 and not (not native and (is_zip or is_tar)):
            self._reserve(name, size)
        if native and self.artifact_kind == 'third-party-source':
            self.finding(name, 'native_payload_in_source_artifact')
            return
        if not native and (is_zip or is_tar):
            if depth >= self.limits.depth:
                self.finding(name, 'nesting_budget')
                return
            if size > self.limits.compressed_bytes:
                self.finding(name, 'compressed_artifact_budget')
                return
            # Anonymous seekable scratch storage enables bounded ZIP preflight
            # and two-pass TAR inspection without extracting member paths.
            with tempfile.TemporaryFile() as temporary:
                temporary.write(prefix)
                remaining = size - len(prefix)
                while remaining:
                    part = stream.read(min(remaining, self.limits.chunk_bytes))
                    if not part:
                        raise ValueError('Truncated compressed artifact')
                    temporary.write(part)
                    remaining -= len(part)
                temporary.seek(0)
                if is_zip:
                    self._zip(name, temporary, depth)
                elif prefix.startswith(b'\x1f\x8b') and not name.lower().endswith(('.tar.gz', '.tgz')):
                    self._raw_compressed(name, temporary, size, depth, kind='gzip')
                elif prefix.startswith(b'BZh') and not name.lower().endswith(('.tar.bz2', '.tbz2', '.tbz')):
                    self._raw_compressed(name, temporary, size, depth, kind='bzip2')
                elif prefix.startswith(b'\xfd7zXZ\x00') and not name.lower().endswith(('.tar.xz', '.txz')):
                    self._raw_compressed(name, temporary, size, depth, kind='xz')
                else:
                    self._tar(name, temporary, size, depth, prefix)
            return
        if prefix.startswith((b'7z\xbc\xaf\x27\x1c', b'Rar!\x1a\x07', b'MSCF')):
            self.finding(name, 'unsupported_compressed_artifact')
            return
        if not native and (suffix in self._TEXT_SOURCE or library_notice or plist_template):
            # Real generated source can have a long printable line (for example
            # Pygments' Cocoa symbol table). Scan the complete bounded text so a
            # credential/URL cannot hide a terminator beyond a streaming window.
            # Native and larger/unknown payloads retain their finite stream guard.
            if size > MAX_ITEM_BYTES:
                self.finding(name, 'text_item_budget')
                return
            data = bytearray(prefix)
            remaining = size - len(prefix)
            while remaining:
                part = stream.read(min(remaining, self.limits.chunk_bytes))
                if not part:
                    raise ValueError('Truncated ordinary source text')
                data.extend(part)
                remaining -= len(part)
                self.counts['streamed_bytes'] += len(part)
            if (library_notice or plist_template) and any(byte < 32 and byte not in {9, 10, 12, 13} for byte in data):
                self.finding(name, 'binary_payload_in_license_notice' if library_notice else 'binary_payload_in_plist_template')
                return
            if plist_template:
                if b'<!ENTITY' in data.upper():
                    self.finding(name, 'plist_entity_declaration')
                    return
                try:
                    document = ET.fromstring(data)
                    if document.tag != 'plist':
                        raise ValueError('Expected an XML property-list root')
                    for element in document.iter():
                        self.text(name + '!plist-text', element.text or '')
                        self.text(name + '!plist-text', element.tail or '')
                        for key, value in element.attrib.items():
                            self.text(name + '!plist-attribute', key)
                            self.text(name + '!plist-attribute', value)
                except (ValueError, ET.ParseError) as exc:
                    self.finding(name, 'invalid_plist_template', type(exc).__name__)
                    return
            self.counts['bounded_text_bytes'] += size
            self.text(name, data.decode('utf8', 'replace'))
            return
        if not native and suffix in self._STRUCTURED:
            if size > MAX_ITEM_BYTES:
                self.finding(name, 'structured_item_budget')
                return
            data = bytearray(prefix)
            remaining = size - len(prefix)
            while remaining:
                part = stream.read(min(remaining, self.limits.chunk_bytes))
                if not part:
                    raise ValueError('Truncated structured artifact')
                data.extend(part)
                remaining -= len(part)
            super().bytes(name, bytes(data), depth)
        else:
            self._stream_text(name, stream, size, prefix, native=native, font=suffix in {'.ttf', '.otf'})

    def _zip(self, name, stream, depth):
        from etlayout.io.archive_directory import inspect_directory
        inspect_directory(stream, max_bytes=self.limits.compressed_bytes,
                          max_entries=self.limits.members,
                          max_directory_bytes=self.limits.directory_bytes)
        with zipfile.ZipFile(stream) as archive:
            self.counts['archives'] += 1
            self.text(name + '!archive-comment', archive.comment.decode('utf8', 'replace'))
            seen = {}
            # Complete declared-size/name/type preflight precedes decompression.
            for member in archive.infolist():
                member_name = name + '!' + member.filename
                if member.orig_filename != member.filename or any(ord(char) < 32 for char in member.filename):
                    self.finding(member_name, 'unsafe_archive_path')
                    raise _ArtifactStop('Ambiguous artifact ZIP name')
                self._name(member.filename, seen, directory=member.is_dir())
                self.text(member_name + '!member-comment', member.comment.decode('utf8', 'replace'))
                self.text(member_name + '!member-extra', member.extra.decode('utf8', 'replace'))
                mode = (member.external_attr >> 16) & 0o170000
                if (mode not in {0, 0o100000, 0o040000} or member.flag_bits & 1
                        or (member.is_dir() and member.file_size)
                        or (mode == 0o040000 and not member.is_dir())):
                    self.finding(member_name, 'unsupported_archive_entry')
                    raise _ArtifactStop('Unsupported artifact ZIP entry')
                if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    self.finding(member_name, 'unsupported_archive_compression')
                    raise _ArtifactStop('Unsupported artifact ZIP compression')
                self._reserve(member_name, member.file_size)
                if member.file_size > max(1, member.compress_size) * self.limits.ratio:
                    self.finding(member_name, 'archive_ratio_budget')
                    raise _ArtifactStop('Artifact compression ratio exceeded')
            for member in archive.infolist():
                if not member.is_dir():
                    with archive.open(member) as payload:
                        before = len(self.findings)
                        self._inspect(name + '!' + member.filename, payload, member.file_size, depth + 1)
                        if len(self.findings) == before and payload.read(1):
                            raise ValueError('ZIP member exceeds its declared size')

    def _tar_entries(self, name, raw, size, prefix):
        raw.seek(0)
        compressed = None
        if prefix.startswith(b'\x1f\x8b'):
            compressed = _ArtifactGZIP(raw, self.limits, lambda: self.finding(
                name, 'unsupported_gzip_concatenation_or_trailing_data'))
        elif prefix.startswith(b'BZh'):
            compressed = _ArtifactBZIP2(raw, self.limits, lambda: self.finding(
                name, 'unsupported_bzip2_concatenation_or_trailing_data'))
        elif prefix.startswith(b'\xfd7zXZ\x00'):
            compressed = _ArtifactXZ(raw, self.limits)
        reader = _ArtifactReader(compressed or raw,
                                 min(self.limits.expanded_bytes, max(1, size) * self.limits.ratio),
                                 self.limits.chunk_bytes)
        global_pax, pending, long_name = {}, {}, None
        metadata_bytes = 0
        entries = 0
        try:
            while True:
                header = reader.exact(512)
                if not any(header):
                    if any(reader.exact(512)):
                        raise ValueError('Invalid TAR terminator')
                    while trailing := reader.read(self.limits.chunk_bytes):
                        if any(trailing):
                            raise ValueError('Hidden trailing TAR payload')
                    return
                member = tarfile.TarInfo.frombuf(header, 'utf8', 'strict')
                # Inspect raw bytes and parsed fields before PAX/GNU overrides.
                # Even unused NUL padding and overridden link/owner fields can
                # contain credentials; a generic owner disposition cannot clear them.
                self.text(name + '!tar-raw-header', header.decode('utf8', 'replace'))
                self.text(name + '!tar-header-name', member.name)
                self.text(name + '!tar-header-linkname', member.linkname)
                self.text(name + '!tar-header-uname', member.uname)
                self.text(name + '!tar-header-gname', member.gname)
                if member.uname not in {'', 'root'} or member.gname not in {'', 'root'}:
                    self.finding(name + '!' + member.name, 'archive_owner_metadata')
                entries += 1
                if entries > self.limits.members:
                    raise ValueError('TAR header count exceeds budget')
                if member.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME}:
                    metadata_bytes += member.size
                    if member.size > self.limits.metadata_bytes or metadata_bytes > self.limits.directory_bytes:
                        raise ValueError('TAR metadata exceeds budget')
                    data = reader.exact(member.size)
                    reader.drain((-member.size) % 512)
                    if member.type == tarfile.GNUTYPE_LONGNAME:
                        if long_name is not None:
                            raise ValueError('Repeated GNU TAR long name')
                        long_name = data.rstrip(b'\0').decode('utf8')
                        self.text(name + '!gnu-longname', long_name)
                    else:
                        attributes = {}
                        cursor = 0
                        while cursor < len(data):
                            space = data.find(b' ', cursor)
                            if space < 0 or space - cursor > 12:
                                raise ValueError('Invalid PAX record')
                            length = int(data[cursor:space])
                            if length <= space - cursor + 2 or cursor + length > len(data):
                                raise ValueError('Invalid PAX record size')
                            record = data[space + 1:cursor + length]
                            if not record.endswith(b'\n') or b'=' not in record:
                                raise ValueError('Invalid PAX record')
                            key, value = record[:-1].split(b'=', 1)
                            key, value = key.decode('utf8'), value.decode('utf8')
                            if key in attributes:
                                raise ValueError('Duplicate PAX metadata key')
                            self.text(name + '!pax-key', key)
                            self.text(name + '!pax-value', value)
                            attributes[key] = value
                            cursor += length
                        if any('sparse' in key.lower() for key in attributes):
                            raise ValueError('Sparse TAR entries are unsupported')
                        (global_pax if member.type == tarfile.XGLTYPE else pending).update(attributes)
                    continue
                if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.LNKTYPE}:
                    raise ValueError('TAR symlinks/special entries are unsupported')
                attributes = {**global_pax, **pending}
                effective_name = attributes.get('path', long_name or member.name)
                effective_size = attributes.get('size', str(member.size))
                if not re.fullmatch(r'\d{1,12}', effective_size):
                    raise ValueError('Invalid TAR effective size')
                effective_size = int(effective_size)
                if effective_size > self.limits.member_bytes:
                    raise ValueError('TAR member exceeds budget')
                if (member.isdir() or member.islnk()) and effective_size:
                    raise ValueError('TAR directory/hardlink has unexpected payload')
                start = reader.position
                yield effective_name, effective_size, member, attributes, reader
                consumed = reader.position - start
                if consumed > effective_size:
                    raise ValueError('TAR member overread')
                reader.drain(effective_size - consumed)
                reader.drain((-effective_size) % 512)
                pending, long_name = {}, None
        finally:
            if compressed is not None and not isinstance(compressed, _ArtifactXZ):
                compressed.close()

    def _gzip_metadata(self, name, stream):
        stream.seek(0)
        header = stream.read(10)
        if len(header) != 10 or header[:2] != b'\x1f\x8b' or header[2] != 8 or header[3] & 0xe0:
            raise ValueError('Invalid GZIP header')
        remaining = self.limits.metadata_bytes
        if header[3] & 4:
            encoded_size = stream.read(2)
            if len(encoded_size) != 2:
                raise ValueError('Truncated GZIP extra header')
            count = struct.unpack('<H', encoded_size)[0]
            if count > remaining:
                raise ValueError('GZIP metadata exceeds budget')
            data = stream.read(count)
            if len(data) != count:
                raise ValueError('Truncated GZIP metadata')
            self.text(name + '!gzip-extra', data.decode('utf8', 'replace'))
            remaining -= count
        for flag, label in [(8, 'filename'), (16, 'comment')]:
            if not header[3] & flag:
                continue
            value = bytearray()
            while True:
                byte = stream.read(1)
                if not byte:
                    raise ValueError('Truncated GZIP metadata')
                if byte == b'\0':
                    break
                remaining -= 1
                if remaining < 0:
                    raise ValueError('GZIP metadata exceeds budget')
                value.extend(byte)
            self.text(name + '!gzip-' + label, value.decode('utf8', 'replace'))

    def _raw_compressed(self, name, stream, size, depth, *, kind):
        """Inspect real decoded standalone data, including CRC and finite tails."""
        self.counts['archives'] += 1
        if kind == 'gzip':
            self._gzip_metadata(name, stream)
        elif kind not in {'bzip2', 'xz'}:
            raise ValueError('Unsupported raw compression kind')
        stream.seek(0)
        ceiling = min(self.limits.member_bytes, self.limits.expanded_bytes,
                      max(1, size) * self.limits.ratio)
        decoder = {'gzip': _ArtifactGZIP, 'bzip2': _ArtifactBZIP2, 'xz': _ArtifactXZ}[kind]
        decoded = decoder(stream, self.limits, lambda: self.finding(
            name, f'unsupported_{kind}_concatenation_or_trailing_data'))
        with tempfile.TemporaryFile() as plain:
            count = 0
            while True:
                block = decoded.read(min(self.limits.chunk_bytes, ceiling - count + 1))
                if not block:
                    break
                count += len(block)
                if count > ceiling:
                    self.finding(name, 'archive_size_budget')
                    raise _ArtifactStop('Expanded compressed data exceeds budget')
                plain.write(block)
            suffix = {'gzip': '.gz', 'bzip2': '.bz2', 'xz': '.xz'}[kind]
            member_name = name[:-len(suffix)] if name.lower().endswith(suffix) else name + '.decoded'
            self._reserve(member_name, count)
            plain.seek(0)
            self._inspect(member_name, plain, count, depth + 1)

    def _tar(self, name, stream, size, depth, prefix):
        self.counts['archives'] += 1
        if prefix.startswith(b'\x1f\x8b'):
            self._gzip_metadata(name, stream)
        seen, regular, links = {}, set(), {}
        for member_name, member_size, member, attributes, _payload in self._tar_entries(name, stream, size, prefix):
            self._name(member_name, seen, directory=member.isdir())
            self._reserve(name + '!' + member_name, member_size)
            self.text(name + '!' + member_name + '!filename', member_name)
            key = member_name.replace('\\', '/').casefold()
            if member.isreg():
                regular.add(key)
            elif member.islnk():
                target = attributes.get('linkpath', member.linkname).replace('\\', '/')
                parts = target.split('/')
                if (not target or target.startswith('/') or any(part == '..' or ':' in part for part in parts)):
                    raise ValueError('Unsafe TAR hardlink target')
                target = '/'.join(part for part in parts if part not in {'', '.'})
                if not target:
                    raise ValueError('Empty TAR hardlink target')
                self.text(name + '!' + member_name + '!link-target', target)
                links[key] = target.casefold()
            if attributes.get('uname', member.uname) not in {'', 'root'} or attributes.get('gname', member.gname) not in {'', 'root'}:
                self.finding(name + '!' + member_name, 'archive_owner_metadata')
            self.json_strings(name + '!' + member_name + '!pax-metadata', attributes)
        # An alias adds no hidden content: its complete regular target is scanned
        # below. Never extract/follow a link against the host filesystem.
        for key in links:
            current, visited = key, set()
            while current in links:
                if current in visited or len(visited) >= 64:
                    raise ValueError('Cyclic or excessive TAR hardlink chain')
                visited.add(current)
                current = links[current]
            if current not in regular:
                raise ValueError('TAR hardlink target is missing or not a regular file')
        self.counts['hardlink_aliases'] += len(links)
        for member_name, member_size, member, _attributes, payload in self._tar_entries(name, stream, size, prefix):
            if member.isreg():
                self._inspect(name + '!' + member_name, payload, member_size, depth + 1)

    def artifact(self, root):
        root = Path(root)
        try:
            if not root.exists():
                self.finding(root.name, 'missing_artifact_input')
                return
            if root.is_symlink() or root.is_junction():
                self.finding(root.name, 'symlink_input')
                return
            candidates = [root] if root.is_file() else root.rglob('*')
            for path in candidates:
                name = path.name if root.is_file() else path.relative_to(root).as_posix()
                if path.is_symlink() or path.is_junction():
                    self.finding(name, 'symlink_input')
                    continue
                if not path.is_file():
                    continue
                size = path.stat().st_size
                if size > self.limits.compressed_bytes:
                    self.finding(name, 'compressed_artifact_budget')
                    continue
                self.counts['files'] += 1
                if self.counts['files'] > self.limits.members:
                    self.finding(name, 'tree_file_budget')
                    return
                with path.open('rb') as stream:
                    self._inspect(name, stream, size, 0)
        except _ArtifactStop:
            return
        except (OSError, ValueError, RuntimeError, EOFError, ImportError, tarfile.TarError,
                zipfile.BadZipFile, lzma.LZMAError, zlib.error) as exc:
            self.finding(root.name, 'artifact_read_error', type(exc).__name__)

    def report(self, **_kwargs):
        result = super().report()
        result.update(mode=self.artifact_kind, limits=vars(self.limits),
                      logical_expanded_bytes=self._logical_bytes,
                      artifact_members=self._members)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'), help='Directory or archive to inspect')
    parser.add_argument('--output', type=Path, help='Write redacted JSON report; keep failed reports private')
    parser.add_argument('--artifact-kind', choices=sorted(ArtifactAudit.KINDS),
                        help='Explicit bounded streaming release-candidate/source-asset inspection; no publication clearance')
    parser.add_argument('--reviewed-findings', type=Path, help='Private exact-asset review; only evidenced benign findings, never structural/credential failures')
    parser.add_argument('--source', action='store_true', help='Exclude generated caches/builds/venvs; omit for a complete publication/export check')
    parser.add_argument('--forbid-text', action='append', default=[], help='Additional confidential literal, supplied locally and never echoed')
    parser.add_argument('--allow-email', action='append', default=[], help='Individually reviewed address that is required public attribution')
    args = parser.parse_args(argv)
    if not args.root.exists():
        parser.error('The input does not exist')
    if args.artifact_kind and args.source:
        parser.error('Release artifact inspection and source exclusions are separate modes')
    if args.reviewed_findings and (not args.artifact_kind or not args.root.is_file()):
        parser.error('Reviewed findings require an explicit release-artifact file scope')
    artifact_before = None
    if args.reviewed_findings:
        if args.root.is_symlink() or args.root.is_junction():
            parser.error('Reviewed artifacts must be ordinary files, without links or junctions')
        declared_size = args.root.stat().st_size
        if declared_size > ArtifactLimits().compressed_bytes:
            parser.error('Reviewed artifact exceeds the finite compressed-byte budget')
        digest = hashlib.sha256()
        inspected_bytes = 0
        with args.root.open('rb') as stream:
            for block in iter(lambda: stream.read(1024**2), b''):
                inspected_bytes += len(block)
                if inspected_bytes > declared_size:
                    parser.error('Reviewed artifact changed size during bounded prehash')
                digest.update(block)
        if inspected_bytes != declared_size:
            parser.error('Reviewed artifact changed size during bounded prehash')
        artifact_before = digest.hexdigest()
    options = dict(forbidden_text=args.forbid_text, allowed_emails=args.allow_email)
    if args.artifact_kind:
        if args.output and args.root.is_dir() and args.output.resolve().is_relative_to(args.root.resolve()):
            parser.error('Keep the release inspection report outside the inspected artifact tree')
        audit = ArtifactAudit(artifact_kind=args.artifact_kind, **options)
        audit.artifact(args.root)
    else:
        audit = PublicationAudit(**options)
        audit.tree(args.root, source=args.source, output=args.output)
    report = audit.report(source=args.source)
    if args.reviewed_findings:
        apply_review = runpy.run_path(str(Path(__file__).parents[1] / 'packaging/publication_review.py'))['apply_review']
        try:
            report = apply_review(report, args.root, args.reviewed_findings, artifact_kind=args.artifact_kind,
                                  scanner_file=Path(__file__), artifact_sha256_before=artifact_before)
        except (ValueError, OSError, KeyError, TypeError):
            audit.finding(args.root.name, 'review_validation_error')
            report = audit.report()
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(clean=report['clean'], mode=report['mode'], counts=report['counts'],
                          finding_kinds=dict(Counter(item['kind'] for item in audit.findings)))))
    return 0 if report['clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
