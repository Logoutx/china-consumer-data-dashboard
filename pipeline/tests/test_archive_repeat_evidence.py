"""A repeated rounded print is valid only with exact current source evidence."""
import copy
import json
from pathlib import Path

import pytest

from pipeline.normalize import NormalizeReport
from pipeline.parsers import nbs_cpi
from pipeline.tests._validate_helpers import make_batch, make_context, touch
from pipeline.tests.test_validate_lifecycle import _small_series
from pipeline.validate.checks.lifecycle import check_archive_release_identity
from pipeline.validate.model import PASS, BLOCK

ROOT = Path(__file__).resolve().parents[2]


def cpi_case(tmp_path):
    sid, period, release = 'nbs-cpi-nonfood-yoy', '2026-05', 'current-cpi'
    html = (ROOT / 'pipeline/fixtures/raw/nbs_cpi/2026-05_cpi.html').read_text()
    values = {r.caliber_hint: r.value for r in nbs_cpi.parse(html).rows if r.source_field == '非食品'}
    real = _small_series(sid, [])
    real['observations'] = [dict(period='2026-04', **values)]
    staged = copy.deepcopy(real)
    staged['observations'].append(dict(period=period, **values))
    ctx = make_context(tmp_path, real_overrides={sid: real}, staged_overrides={sid: staged},
        batch=make_batch([touch(sid, period, source_field='非食品', **values)], release_id=release, source='nbs-cpi'),
        normalize_report=NormalizeReport(new_observations=[(sid, period)]))
    folder = ctx.archive_dir / 'nbs-cpi'
    folder.mkdir(parents=True, exist_ok=True)
    capture = folder / f'{release}.html'
    capture.write_text(html)
    return ctx, capture


def test_repeated_cpi_verified_current_release_passes(tmp_path):
    ctx, _ = cpi_case(tmp_path)
    assert check_archive_release_identity(ctx).status == PASS


@pytest.mark.parametrize('mutation', ['prior_period', 'wrong_label', 'wrong_value', 'malformed'])
def test_cpi_evidence_fails_closed(tmp_path, mutation):
    ctx, path = cpi_case(tmp_path)
    content = path.read_text()
    if mutation == 'prior_period':
        content = content.replace('2026年5月份居民消费价格', '2026年4月份居民消费价格')
    elif mutation == 'wrong_label':
        ctx.batch.items[0].source_field = '食品'
    elif mutation == 'wrong_value':
        # Keep proposed period identical to previous, but neither agrees with source.
        sid = 'nbs-cpi-nonfood-yoy'
        data = ctx.load(sid)
        for obs in data['observations']:
            obs['mom'] = 123
    else:
        content = 'not a release'
    path.write_text(content)
    assert check_archive_release_identity(ctx).status == BLOCK


def dg_case(tmp_path, sid='nbs-ppi-consumer-yoy'):
    period, release, iid = '2026-10', 'dg-refresh-2026-11-13', 'a' * 32
    real = _small_series(sid, [])
    real['observations'] = [dict(period='2026-09', m=99.9, m_yoy=-0.1, src='dg:'+iid)]
    staged = copy.deepcopy(real)
    staged['observations'].append(dict(period=period, m=99.9, m_yoy=-0.1))
    ctx = make_context(tmp_path, real_overrides={sid: real}, staged_overrides={sid: staged},
        batch=make_batch([touch(sid, period, m=99.9, m_yoy=-0.1)], release_id=release, source='dg-refresh'),
        normalize_report=NormalizeReport(new_observations=[(sid, period)]))
    folder = ctx.archive_dir / 'dg'
    folder.mkdir(parents=True, exist_ok=True)
    capture = folder / 'values_current.json'
    capture.write_text(json.dumps({'request': {'id': iid}, 'response': {'data': [{'dt':'202610MM','v':'99.9'}]}}))
    manifest = folder / f'manifest_{release}.json'
    manifest.write_text(json.dumps({'release_id': release, 'captures':[capture.name]}))
    return ctx, capture, manifest


@pytest.mark.parametrize('sid', ['nbs-cpi-nonfood-yoy', 'nbs-ppi-consumer-yoy'])
def test_repeated_dg_price_passes_with_period_indicator_value_evidence(tmp_path, sid):
    ctx, _, _ = dg_case(tmp_path, sid)
    assert check_archive_release_identity(ctx).status == PASS


@pytest.mark.parametrize('mutation', ['prior_period', 'wrong_indicator', 'wrong_value', 'missing', 'unlisted', 'malformed', 'wrong_manifest'])
def test_dg_evidence_fails_closed(tmp_path, mutation):
    ctx, capture, manifest = dg_case(tmp_path)
    payload = json.loads(capture.read_text())
    if mutation == 'prior_period': payload['response']['data'][0]['dt'] = '202609MM'
    elif mutation == 'wrong_indicator': payload['request']['id'] = 'b'*32
    elif mutation == 'wrong_value': payload['response']['data'][0]['v'] = '100.2'
    elif mutation == 'missing':
        capture.unlink()
        assert check_archive_release_identity(ctx).status == BLOCK
        return
    elif mutation == 'unlisted':
        (capture.parent/'values_other.json').write_text('{}')
        manifest.write_text(json.dumps({'release_id':ctx.batch.release_id,'captures':['values_other.json']}))
    elif mutation == 'malformed': payload = {'response':{'data':'bad'}}
    elif mutation == 'wrong_manifest':
        manifest.write_text(json.dumps({'release_id':'old','captures':[capture.name]}))
    capture.write_text(json.dumps(payload))
    assert check_archive_release_identity(ctx).status == BLOCK


def test_conflicting_cpi_duplicate_cannot_clear_stale_block(tmp_path, monkeypatch):
    ctx, capture = cpi_case(tmp_path)
    parsed = nbs_cpi.parse(capture.read_text())
    duplicate = copy.deepcopy(next(r for r in parsed.rows if r.source_field == '非食品'))
    duplicate.value += 1
    parsed.rows.insert(0, duplicate)
    monkeypatch.setattr(nbs_cpi, 'parse', lambda _: parsed)
    assert check_archive_release_identity(ctx).status == BLOCK


def test_dg_extra_measure_cannot_clear_stale_block(tmp_path):
    ctx, _, _ = dg_case(tmp_path)
    for obs in ctx.load('nbs-ppi-consumer-yoy')['observations']:
        obs['mom'] = 0.1
    assert check_archive_release_identity(ctx).status == BLOCK


def test_conflicting_dg_duplicate_cannot_clear_stale_block(tmp_path):
    ctx, capture, _ = dg_case(tmp_path)
    payload = json.loads(capture.read_text())
    payload['response']['data'].append({'dt': '202610MM', 'v': '100.2'})
    capture.write_text(json.dumps(payload))
    assert check_archive_release_identity(ctx).status == BLOCK
