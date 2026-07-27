from __future__ import annotations

from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.control import router as control_router
from app.routes.delivery import router as delivery_router
from app.storage.blocklist_store import BlocklistStore


class _FakeMonitor:
    def audit(self, *args, **kwargs) -> None:
        pass


class BlocklistRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tempdir.name) / 'blocklist.db'
        self.store = BlocklistStore(db_path)

        self.application = FastAPI()
        self.application.include_router(delivery_router)
        self.application.include_router(control_router)

        fake_state = SimpleNamespace(blocklist_store=self.store)
        self.delivery_state_patch = patch('app.routes.delivery.STATE', fake_state)
        self.control_state_patch = patch('app.routes.control.STATE', fake_state)
        self.monitor_patch = patch('app.routes.control.RUNTIME_MONITOR', _FakeMonitor())
        self.delivery_state_patch.start()
        self.control_state_patch.start()
        self.monitor_patch.start()
        self.client = TestClient(self.application)

    def tearDown(self) -> None:
        self.client.close()
        self.delivery_state_patch.stop()
        self.control_state_patch.stop()
        self.monitor_patch.stop()
        self.store.close()
        self._tempdir.cleanup()

    def test_check_reports_missing_then_present_after_add(self) -> None:
        payload = {'company': '甲公司', 'title': '后端工程师'}
        first = self.client.post('/blocklist/check', json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()['blocked'])

        added = self.client.post(
            '/blocklist/add',
            json={**payload, 'reason': 'below_threshold', 'score': 40},
        )
        self.assertEqual(added.status_code, 200)
        self.assertTrue(added.json()['created'])

        again = self.client.post('/blocklist/check', json=payload)
        self.assertTrue(again.json()['blocked'])
        self.assertEqual(again.json()['entry']['reason'], 'below_threshold')

    def test_add_is_idempotent_and_updates_reason(self) -> None:
        payload = {'company': '乙公司', 'title': '测试'}
        self.client.post('/blocklist/add', json={**payload, 'reason': 'below_threshold'})
        second = self.client.post(
            '/blocklist/add',
            json={**payload, 'reason': 'ai_rejected', 'aiReason': '不匹配'},
        )
        self.assertFalse(second.json()['created'])
        entry = self.client.post('/blocklist/check', json=payload).json()['entry']
        self.assertEqual(entry['reason'], 'ai_rejected')
        self.assertEqual(entry['ai_reason'], '不匹配')

    def test_admin_list_and_manual_add_and_delete(self) -> None:
        added = self.client.post(
            '/api/admin/blocklist/add',
            json={'company': '丙公司', 'title': '运维', 'note': '不合适'},
        )
        self.assertEqual(added.status_code, 200)
        company_key = added.json()['companyKey']

        listing = self.client.get('/api/admin/blocklist')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()['total'], 1)
        self.assertEqual(listing.json()['entries'][0]['reason'], 'manual')

        deleted = self.client.post(
            '/api/admin/blocklist/delete',
            json={'companyKeys': [company_key]},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()['deleted'], 1)
        self.assertEqual(self.store.count(), 0)

    def test_admin_list_filters_by_keyword_and_reason(self) -> None:
        self.client.post('/api/admin/blocklist/add', json={'company': '阿尔法科技', 'title': '前端'})
        self.client.post(
            '/blocklist/add',
            json={'company': '贝塔网络', 'title': '后端', 'reason': 'ai_rejected'},
        )

        by_keyword = self.client.get('/api/admin/blocklist', params={'keyword': '阿尔法'})
        self.assertEqual(by_keyword.json()['total'], 1)
        self.assertEqual(by_keyword.json()['entries'][0]['company'], '阿尔法科技')

        by_reason = self.client.get('/api/admin/blocklist', params={'reason': 'ai_rejected'})
        self.assertEqual(by_reason.json()['total'], 1)
        self.assertEqual(by_reason.json()['entries'][0]['company'], '贝塔网络')

    def test_manual_add_requires_company_and_title(self) -> None:
        missing_company = self.client.post('/api/admin/blocklist/add', json={'title': '岗位'})
        self.assertEqual(missing_company.status_code, 400)
        missing_title = self.client.post('/api/admin/blocklist/add', json={'company': '公司'})
        self.assertEqual(missing_title.status_code, 400)

    def test_delete_requires_selection(self) -> None:
        response = self.client.post('/api/admin/blocklist/delete', json={'companyKeys': []})
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
