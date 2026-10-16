# SPDX-License-Identifier: MPL-2.0
"""Bind private review dispositions to exact inspected release bytes.

This does not authorize publication. The owner release preflight checks authority,
CI, the committed binary policy and all other compliance evidence first.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

MAX_REVIEW_BYTES = 8 * 1024**2
MAX_SOURCE_REVIEW_BYTES = 32 * 1024**2
MAX_EVIDENCE_BYTES = 64 * 1024**2
MAX_TOTAL_EVIDENCE = 256 * 1024**2
MAX_EVIDENCE_FILES = 100
DISPOSITIONS = {
    'email': {'required_attribution', 'upstream_contact', 'public_test_fixture',
              'non_address_binary_pattern', 'resource_identifier'},
    'personal_path': {'upstream_build_provenance', 'public_test_fixture',
                      'public_documentation_url'},
    'private_address': {'public_test_fixture', 'documented_network_default',
                        'non_address_numeric_reference'},
    'archive_owner_metadata': {'upstream_archive_metadata'},
    'private_key': {'key_format_label', 'public_test_key'},
}
# Upstream test inputs sometimes intentionally contain fictional credentials.
# Only corresponding-source reviews may classify these individually, with exact
# upstream source context; this does not clear live credentials or binary data.
SOURCE_TEST_DISPOSITIONS = {
    'authenticated_url': {'public_test_authentication_example'},
    'secret_assignment': {'public_test_secret_literal'},
}


def sha256(path, *, max_bytes=2 * 1024**3):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        total = 0
        for chunk in iter(lambda: stream.read(1024**2), b''):
            total += len(chunk)
            _require(total <= max_bytes, 'Hashed review input exceeds its bounded budget')
            digest.update(chunk)
    return digest.hexdigest()


def finding_id(finding):
    return hashlib.sha256(json.dumps(finding, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _private_file(base, name):
    _require(isinstance(name, str) and '\\' not in name, 'Unsafe review evidence name')
    parts = PurePosixPath(name).parts
    _require(parts and not name.startswith('/') and all(
        part not in {'', '.', '..'} and ':' not in part for part in parts),
        'Unsafe review evidence name')
    candidate = base.joinpath(*parts)
    _require(not any(part.is_symlink() or part.is_junction()
                     for part in (candidate, *candidate.parents) if part != base.parent),
             'Review evidence cannot use links')
    _require(candidate.resolve().is_relative_to(base.resolve()) and candidate.is_file(),
             'Missing or unsafe review evidence')
    return candidate


def apply_review(report, artifact, manifest_path, *, artifact_kind, scanner_file,
                 artifact_sha256_before):
    """Resolve only individually evidenced benign findings; never parser failures.

    Tokens, live credentials, malformed containers, resource limits and unread
    content cannot be cleared. Exact published fictional test inputs may be
    classified only in third-party source. Changed, missing or extra rows fail.
    The report retains every original redacted finding for private auditability.
    """
    artifact, manifest_path = Path(artifact), Path(manifest_path)
    review_budget = MAX_SOURCE_REVIEW_BYTES if artifact_kind == 'third-party-source' else MAX_REVIEW_BYTES
    _require(artifact_kind in {'binary-candidate', 'third-party-source'} and artifact.is_file()
             and not artifact.is_symlink() and not artifact.is_junction(),
             'Review is available only for exact release artifact files')
    _require(manifest_path.is_file() and not manifest_path.is_symlink()
             and not manifest_path.is_junction() and manifest_path.stat().st_size <= review_budget,
             'Review manifest is missing, linked or oversized')
    with manifest_path.open('rb') as stream:
        manifest_bytes = stream.read(review_budget + 1)
    _require(len(manifest_bytes) <= review_budget, 'Review manifest grew beyond its budget')
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    review = json.loads(manifest_bytes.decode('utf-8'))
    _require(type(review) is dict and review.get('schema_version') == 1
             and review.get('artifact_kind') == artifact_kind,
             'Review schema or artifact scope mismatch')
    _require(re.fullmatch('[0-9a-f]{64}', review.get('artifact_sha256', '')) is not None
             and artifact_sha256_before == review['artifact_sha256'] == sha256(artifact),
             'Artifact bytes changed or differ from the reviewed artifact')
    _require(review.get('scanner_sha256') == sha256(scanner_file)
             and review.get('review_code_sha256') == sha256(__file__),
             'Review does not bind the current inspection code')
    inspector = Path(scanner_file).parents[1] / 'src/etlayout/io/archive_directory.py'
    _require(review.get('directory_inspector_sha256') == sha256(inspector),
             'Review does not bind the current archive-directory inspector')
    entries = review.get('findings')
    _require(type(entries) is list and len(entries) == len(report['findings']),
             'Review finding coverage mismatch')
    actual = {finding_id(finding): finding for finding in report['findings']}
    _require(len(actual) == len(report['findings']), 'Duplicate inspected findings')
    seen, files, dispositions = set(), {}, []
    for entry in entries:
        _require(type(entry) is dict and type(entry.get('finding')) is dict,
                 'Invalid review finding record')
        finding = entry['finding']
        key = finding_id(finding)
        _require(key in actual and key not in seen and actual[key] == finding,
                 'Stale, duplicate or unexpected review finding')
        seen.add(key)
        category = entry.get('disposition')
        permitted = DISPOSITIONS.get(finding.get('kind'), set())
        if artifact_kind == 'third-party-source':
            permitted = permitted | SOURCE_TEST_DISPOSITIONS.get(finding.get('kind'), set())
        _require(category in permitted,
                 'This finding kind or disposition cannot be cleared by review')
        _require(isinstance(entry.get('rationale'), str) and 16 <= len(entry['rationale']) <= 4096,
                 'Each disposition needs a substantive bounded rationale')
        evidence = entry.get('evidence')
        _require(type(evidence) is list and 1 <= len(evidence) <= 10,
                 'Each disposition needs exact private evidence')
        hashes = []
        for item in evidence:
            _require(type(item) is dict and re.fullmatch('[0-9a-f]{64}', item.get('sha256', '')),
                     'Invalid review evidence hash')
            path = _private_file(manifest_path.parent, item.get('name'))
            if path not in files:
                _require(path.stat().st_size <= MAX_EVIDENCE_BYTES,
                         'Review evidence exceeds its per-file budget')
                files[path] = (sha256(path, max_bytes=MAX_EVIDENCE_BYTES), path.stat().st_size)
                _require(len(files) <= MAX_EVIDENCE_FILES and sum(v[1] for v in files.values()) <= MAX_TOTAL_EVIDENCE,
                         'Review evidence exceeds aggregate budgets')
            _require(files[path][0] == item['sha256'], 'Review evidence checksum changed')
            hashes.append(item['sha256'])
        dispositions.append({'finding_id': key, 'disposition': category, 'evidence_sha256': hashes})
    _require(seen == actual.keys(), 'Unreviewed findings remain')
    _require(all(sha256(path, max_bytes=MAX_EVIDENCE_BYTES) == value[0] for path, value in files.items())
             and sha256(artifact) == artifact_sha256_before
             and sha256(manifest_path, max_bytes=review_budget) == manifest_sha256,
             'Artifact, review manifest or evidence changed while reviewing')
    result = dict(report)
    result.update(pattern_clean=report['clean'], clean=True, unresolved_findings=[],
                  review={'schema_version': 1, 'artifact_sha256': artifact_sha256_before,
                          'manifest_sha256': manifest_sha256,
                          'reviewed_findings': dispositions,
                          'publication_authorized': False})
    return result
