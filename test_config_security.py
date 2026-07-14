from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import admin_store
import config


ROOT = Path(__file__).resolve().parent


class ConfigSecurityTest(unittest.TestCase):
    def test_environment_values_override_legacy_json(self):
        legacy = {
            'llm': {
                'api_base': 'https://legacy.example/v1',
                'api_key': 'legacy-key',
            }
        }
        environment = {
            'GOODJOB_LLM_API_BASE': 'https://env.example/v1',
            'GOODJOB_LLM_API_KEY': 'env-key',
        }
        with (
            patch.object(config, '_load_raw_user_config', return_value=legacy),
            patch.dict(os.environ, environment, clear=False),
        ):
            loaded = config.load_user_config()

        self.assertEqual(loaded['llm']['api_base'], environment['GOODJOB_LLM_API_BASE'])
        self.assertEqual(loaded['llm']['api_key'], environment['GOODJOB_LLM_API_KEY'])

    def test_public_config_redacts_api_endpoint_and_key(self):
        effective = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        effective['llm']['api_base'] = 'https://private.example/v1'
        effective['llm']['api_key'] = 'private-key'
        config_path = ROOT / '.test-user-config-security.json'
        with (
            patch.object(admin_store.Config, 'reload', return_value=effective),
            patch.object(admin_store, 'CONFIG_PATH', config_path),
        ):
            result = admin_store.get_public_config()

        self.assertEqual(result['config']['llm']['api_base'], '')
        self.assertEqual(result['config']['llm']['api_key'], '')
        self.assertTrue(result['apiBaseConfigured'])
        self.assertTrue(result['apiKeyConfigured'])

    def test_save_config_does_not_persist_secrets(self):
        effective = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        payload_config = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        payload_config['llm']['api_base'] = 'https://should-not-persist.example/v1'
        payload_config['llm']['api_key'] = 'should-not-persist'
        config_path = ROOT / '.test-user-config-security.json'
        try:
            with (
                patch.object(admin_store.Config, 'reload', return_value=effective),
                patch.object(admin_store, 'CONFIG_PATH', config_path),
            ):
                admin_store.save_config({'config': payload_config})
            saved = json.loads(config_path.read_text(encoding='utf-8'))
        finally:
            config_path.unlink(missing_ok=True)
            config_path.with_name(f'.{config_path.name}.tmp').unlink(missing_ok=True)

        self.assertEqual(saved['llm']['api_base'], '')
        self.assertEqual(saved['llm']['api_key'], '')


if __name__ == '__main__':
    unittest.main()
