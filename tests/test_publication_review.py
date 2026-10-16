# SPDX-License-Identifier: MPL-2.0
"""Exact benign-finding review and fail-closed drift contracts; no publication."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publication_review', ROOT / 'packaging/publication_review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


@pytest.fixture
def inspected(tmp_path):
    artifact = tmp_path / 'candidate.zip'
    artifact.write_bytes(b'bounded synthetic bytes')
    proof = tmp_path / 'classification.json'
    proof.write_text('{"context":"verified synthetic notice"}')
    finding = {'path': 'candidate.zip!library.dll!native-string', 'kind': 'email',
               'fingerprint': 'a' * 16, 'line': 10}
    report = {'schema_version': 1, 'clean': False, 'findings': [finding], 'counts': {'files': 1}}
    record = {'finding': finding, 'disposition': 'required_attribution',
              'rationale': 'Required upstream copyright attribution, checked against original source.',
              'evidence': [{'name': proof.name, 'sha256': review.sha256(proof)}]}
    manifest = {'schema_version': 1, 'artifact_kind': 'binary-candidate',
                'artifact_sha256': review.sha256(artifact),
                'scanner_sha256': review.sha256(ROOT / 'scripts/audit_publication.py'),
                'review_code_sha256': review.sha256(ROOT / 'packaging/publication_review.py'),
                'directory_inspector_sha256': review.sha256(ROOT / 'src/etlayout/io/archive_directory.py'),
                'findings': [record]}
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(manifest))
    return artifact, proof, report, manifest, path


def apply(inputs):
    artifact, _proof, report, manifest, path = inputs
    path.write_text(json.dumps(manifest))
    return review.apply_review(report, artifact, path, artifact_kind='binary-candidate',
                               scanner_file=ROOT / 'scripts/audit_publication.py',
                               artifact_sha256_before=manifest['artifact_sha256'])


def test_exact_evidenced_review_retains_original_findings_and_does_not_authorize(inspected):
    result = apply(inspected)
    assert result['clean'] and not result['pattern_clean']
    assert result['findings'] == inspected[2]['findings']
    assert result['unresolved_findings'] == []
    assert not result['review']['publication_authorized']
    assert result['review']['reviewed_findings'][0]['finding_id'] == review.finding_id(result['findings'][0])
    assert 'Required upstream copyright' not in json.dumps(result)


@pytest.mark.parametrize('field', ['artifact_sha256', 'scanner_sha256', 'review_code_sha256',
                                   'directory_inspector_sha256'])
def test_review_rejects_changed_artifact_or_inspection_code(inspected, field):
    inspected[3][field] = 'f' * 64
    with pytest.raises(ValueError):
        apply(inspected)


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate', 'line', 'category', 'rationale', 'evidence'])
def test_review_rejects_incomplete_or_relabelled_findings(inspected, change):
    rows = inspected[3]['findings']
    if change == 'missing':
        rows.clear()
    elif change in {'extra', 'duplicate'}:
        rows.append(dict(rows[0]))
    elif change == 'line':
        rows[0] = {**rows[0], 'finding': {**rows[0]['finding'], 'line': 11}}
    elif change == 'category':
        rows[0]['disposition'] = 'ignore'
    elif change == 'rationale':
        rows[0]['rationale'] = ''
    else:
        rows[0]['evidence'] = []
    with pytest.raises(ValueError):
        apply(inspected)


@pytest.mark.parametrize('kind', ['github_token', 'cloud_access_key', 'authenticated_url',
                                  'secret_assignment', 'artifact_read_error', 'archive_size_budget',
                                  'unsafe_archive_path', 'unsupported_archive_entry'])
def test_credentials_and_incomplete_inspections_are_never_waived(inspected, kind):
    inspected[2]['findings'][0]['kind'] = kind
    with pytest.raises(ValueError, match='cannot be cleared'):
        apply(inspected)


@pytest.mark.parametrize('change', ['changed', 'missing', 'parent', 'absolute'])
def test_review_evidence_is_hash_bound_and_confined(inspected, change):
    proof = inspected[1]
    if change == 'changed':
        proof.write_text('changed after the review')
    elif change == 'missing':
        proof.unlink()
    else:
        inspected[3]['findings'][0]['evidence'][0]['name'] = '../outside' if change == 'parent' else 'C:/outside'
    with pytest.raises(ValueError):
        apply(inspected)


def test_larger_review_budget_is_scoped_to_complete_third_party_source(inspected, monkeypatch):
    monkeypatch.setattr(review, 'MAX_REVIEW_BYTES', 100)
    monkeypatch.setattr(review, 'MAX_SOURCE_REVIEW_BYTES', 8192)
    with pytest.raises(ValueError, match='oversized'):
        apply(inspected)
    artifact, _proof, report, manifest, path = inspected
    manifest['artifact_kind'] = 'third-party-source'
    path.write_text(json.dumps(manifest))
    result = review.apply_review(report, artifact, path, artifact_kind='third-party-source',
                                scanner_file=ROOT / 'scripts/audit_publication.py',
                                artifact_sha256_before=manifest['artifact_sha256'])
    assert result['clean'] and len(result['review']['reviewed_findings']) == 1
    assert result['review']['publication_authorized'] is False


def test_review_manifest_changed_after_parsing_is_rejected(inspected, monkeypatch):
    original = review.sha256
    seen = 0

    def digest(path, **kwargs):
        nonlocal seen
        if Path(path) == inspected[1]:
            seen += 1
            if seen == 2:
                inspected[4].write_text('{"changed":"after initial validation"}')
        return original(path, **kwargs)

    monkeypatch.setattr(review, 'sha256', digest)
    with pytest.raises(ValueError, match='changed while reviewing'):
        apply(inspected)


def test_numeric_reference_needs_exact_context_instead_of_fake_network_claim(inspected):
    inspected[2]['findings'][0]['kind'] = 'private_address'
    inspected[3]['findings'][0]['disposition'] = 'non_address_numeric_reference'
    inspected[3]['findings'][0]['rationale'] = 'Exact upstream source context identifies a numbered scientific reference, not a host address.'
    result = apply(inspected)
    assert result['review']['reviewed_findings'][0]['disposition'] == 'non_address_numeric_reference'
    assert result['clean'] and not result['review']['publication_authorized']


@pytest.mark.parametrize('kind,category', [
    ('authenticated_url', 'public_test_authentication_example'),
    ('secret_assignment', 'public_test_secret_literal'),
])
def test_public_fictional_test_inputs_require_exact_source_only_review(inspected, kind, category):
    artifact, _proof, report, manifest, path = inspected
    report['findings'][0]['kind'] = kind
    manifest['findings'][0]['disposition'] = category
    manifest['findings'][0]['rationale'] = 'Exact published upstream test source confirms fictional fixed test data; no live credential or runtime secret.'
    with pytest.raises(ValueError, match='cannot be cleared'):
        apply(inspected)
    manifest['artifact_kind'] = 'third-party-source'
    path.write_text(json.dumps(manifest))
    result = review.apply_review(report, artifact, path, artifact_kind='third-party-source',
                                scanner_file=ROOT / 'scripts/audit_publication.py',
                                artifact_sha256_before=manifest['artifact_sha256'])
    assert result['clean'] and result['findings'] == report['findings']
    assert not result['review']['publication_authorized']
    manifest['findings'][0]['evidence'].clear()
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='exact private evidence'):
        review.apply_review(report, artifact, path, artifact_kind='third-party-source',
                            scanner_file=ROOT / 'scripts/audit_publication.py',
                            artifact_sha256_before=manifest['artifact_sha256'])


@pytest.mark.parametrize('kind', ['github_token', 'cloud_access_key', 'artifact_read_error', 'unsupported_archive_entry'])
def test_source_test_disposition_cannot_clear_tokens_or_unread_content(inspected, kind):
    artifact, _proof, report, manifest, path = inspected
    report['findings'][0]['kind'] = kind
    manifest['artifact_kind'] = 'third-party-source'
    manifest['findings'][0]['disposition'] = 'public_test_secret_literal'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='cannot be cleared'):
        review.apply_review(report, artifact, path, artifact_kind='third-party-source',
                            scanner_file=ROOT / 'scripts/audit_publication.py',
                            artifact_sha256_before=manifest['artifact_sha256'])
