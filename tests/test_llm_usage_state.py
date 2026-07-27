from __future__ import annotations

from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from app.config import Config
from app.state import ApplicationState


class LLMUsageApplicationStateTests(unittest.TestCase):
    def setUp(self) -> None:
        token = uuid.uuid4().hex
        self.root = Path(__file__).parent
        self.first_path = self.root / f'.test_llm_state_{token}_first.db'
        self.second_path = self.root / f'.test_llm_state_{token}_second.db'
        self.state = None

    def tearDown(self) -> None:
        if self.state is not None:
            self.state.close()
        for database_path in (self.first_path, self.second_path):
            for suffix in ('', '-shm', '-wal'):
                Path(f'{database_path}{suffix}').unlink(missing_ok=True)

    def test_startup_initializes_and_switches_usage_store_with_delivery_database(self) -> None:
        state = self.state = ApplicationState(self.root)
        with patch.object(Config, 'reload'):
            with patch.object(Config, 'backend', {
                'delivery_db_path': self.first_path.name,
                'daily_greet_limit': 90,
            }):
                state.startup()

            first_delivery = state.delivery_store
            first_usage = state.llm_usage_store
            self.assertEqual(first_usage.db_path, first_delivery.db_path)

            with patch.object(Config, 'backend', {
                'delivery_db_path': self.second_path.name,
                'daily_greet_limit': 90,
            }):
                state.startup()

        self.assertIsNot(state.delivery_store, first_delivery)
        self.assertIsNot(state.llm_usage_store, first_usage)
        self.assertEqual(state.delivery_store.db_path, self.second_path)
        self.assertEqual(state.llm_usage_store.db_path, self.second_path)


if __name__ == '__main__':
    unittest.main()
