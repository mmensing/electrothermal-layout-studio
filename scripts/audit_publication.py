# SPDX-License-Identifier: MPL-2.0
"""Inspect source trees and distribution archives before sharing them.

This is a conservative local check, not a proof of provenance, license compliance,
or absence of every secret. Review legitimate findings privately. Do not upload
findings containing sensitive material to a public issue or CI artifact.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import struct
import tarfile
import tempfile
import warnings
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
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
    'email': re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
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

    def text(self, name: str, value: str):
        for kind, pattern in _PATTERNS.items():
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
                self.finding(name, kind, matched, line=value.count('\n', 0, match.start()) + 1)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'), help='Directory or archive to inspect')
    parser.add_argument('--output', type=Path, help='Write redacted JSON report; keep failed reports private')
    parser.add_argument('--source', action='store_true', help='Exclude generated caches/builds/venvs; omit for a complete publication/export check')
    parser.add_argument('--forbid-text', action='append', default=[], help='Additional confidential literal, supplied locally and never echoed')
    parser.add_argument('--allow-email', action='append', default=[], help='Individually reviewed address that is required public attribution')
    args = parser.parse_args(argv)
    if not args.root.exists():
        parser.error('The input does not exist')
    audit = PublicationAudit(forbidden_text=args.forbid_text, allowed_emails=args.allow_email)
    audit.tree(args.root, source=args.source, output=args.output)
    report = audit.report(source=args.source)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps(dict(clean=report['clean'], mode=report['mode'], counts=report['counts'],
                          finding_kinds=dict(Counter(item['kind'] for item in audit.findings)))))
    return 0 if report['clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
