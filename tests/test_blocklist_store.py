from __future__ import annotations

from app.storage.blocklist_store import BlocklistStore
from app.storage.delivery_store import delivery_key


def _store(tmp_path):
    return BlocklistStore(tmp_path / 'blocklist.db')


def test_block_and_is_blocked_round_trip(tmp_path):
    store = _store(tmp_path)
    result = store.block(company='甲公司', title='后端工程师', reason='below_threshold', score=40)
    assert result['success'] is True
    assert result['created'] is True

    status = store.is_blocked('甲公司', '后端工程师')
    assert status['blocked'] is True
    assert status['entry']['reason'] == 'below_threshold'
    assert status['entry']['score'] == 40


def test_block_is_idempotent_and_keeps_created_at(tmp_path):
    store = _store(tmp_path)
    first = store.block(company='甲公司', title='后端', reason='below_threshold', score=20)
    created_at = store.is_blocked('甲公司', '后端')['entry']['created_at']
    second = store.block(company='甲公司', title='后端', reason='ai_rejected', ai_reason='不匹配')

    assert first['created'] is True
    assert second['created'] is False
    entry = store.is_blocked('甲公司', '后端')['entry']
    # 同键刷新原因与 AI 理由，但保留最初的 created_at。
    assert entry['reason'] == 'ai_rejected'
    assert entry['ai_reason'] == '不匹配'
    assert entry['created_at'] == created_at
    assert store.count() == 1


def test_block_granularity_is_company_plus_title(tmp_path):
    store = _store(tmp_path)
    store.block(company='甲公司', title='后端', reason='below_threshold')
    # 同公司不同岗位不应被误伤。
    assert store.is_blocked('甲公司', '后端')['blocked'] is True
    assert store.is_blocked('甲公司', '前端')['blocked'] is False
    assert store.count() == 1


def test_missing_company_is_rejected(tmp_path):
    store = _store(tmp_path)
    result = store.block(company='   ', title='后端', reason='manual')
    assert result['success'] is False
    assert result['reason'] == 'missing_company'
    assert store.count() == 0


def test_unknown_reason_falls_back_to_manual(tmp_path):
    store = _store(tmp_path)
    store.block(company='甲公司', title='后端', reason='something_else')
    assert store.is_blocked('甲公司', '后端')['entry']['reason'] == 'manual'


def test_list_blocked_filters_by_keyword_and_reason(tmp_path):
    store = _store(tmp_path)
    store.block(company='阿里巴巴', title='后端', reason='below_threshold')
    store.block(company='腾讯', title='算法', reason='ai_rejected')
    store.block(company='字节跳动', title='测试', reason='manual')

    assert {entry['company'] for entry in store.list_blocked()} == {'阿里巴巴', '腾讯', '字节跳动'}
    assert [entry['company'] for entry in store.list_blocked(keyword='腾讯')] == ['腾讯']
    assert [entry['company'] for entry in store.list_blocked(reason='manual')] == ['字节跳动']
    assert store.list_blocked(reason='not_a_reason', keyword='阿里')[0]['company'] == '阿里巴巴'


def test_unblock_by_company_keys(tmp_path):
    store = _store(tmp_path)
    store.block(company='甲公司', title='后端', reason='below_threshold')
    store.block(company='乙公司', title='前端', reason='ai_rejected')
    key = delivery_key('甲公司', '后端')

    result = store.unblock(company_keys=[key])
    assert result['deleted'] == 1
    assert store.is_blocked('甲公司', '后端')['blocked'] is False
    assert store.is_blocked('乙公司', '前端')['blocked'] is True


def test_unblock_by_jobs(tmp_path):
    store = _store(tmp_path)
    store.block(company='甲公司', title='后端', reason='below_threshold')
    result = store.unblock(jobs=[('甲公司', '后端')])
    assert result['deleted'] == 1
    assert store.count() == 0


def test_unblock_without_targets_is_noop(tmp_path):
    store = _store(tmp_path)
    store.block(company='甲公司', title='后端', reason='below_threshold')
    result = store.unblock()
    assert result['deleted'] == 0
    assert store.count() == 1
