"""验证网页简历管理、LLM 当前简历选择及关键词配置的核心约束。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import admin_store
import config
import core
import resume_store


class ResumeManagementTests(unittest.TestCase):
    """使用独立目录验证简历保存、选择、过滤和输入安全规则。"""

    def setUp(self):
        """把配置与简历目录重定向到当前测试进程的隔离目录。"""
        tests_dir = Path(__file__).resolve().parent
        self.test_root = tests_dir / f'.resume-management-{os.getpid()}'
        self._remove_test_root()
        self.resume_dir = self.test_root / 'resumes'
        self.config_path = self.test_root / 'user_config.json'
        self.resume_dir.mkdir(parents=True)

        # 同时替换各模块已经导入的路径常量，确保没有读写仓库中的真实用户数据。
        self._patchers = [
            patch.object(config, 'ROOT', self.test_root),
            patch.object(admin_store, 'CONFIG_PATH', self.config_path),
            patch.object(resume_store, 'ROOT', self.test_root),
            patch.object(resume_store, 'RESUME_DIR', self.resume_dir),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.addCleanup(self._restore_environment)

        isolated_config = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        isolated_config['resume_name'] = 'resume.md'
        self.config_path.write_text(
            json.dumps(isolated_config, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        config.Config.reload()

    def _restore_environment(self):
        """撤销路径替换、重载真实配置，并清理测试目录。"""
        for patcher in reversed(self._patchers):
            patcher.stop()
        try:
            config.Config.reload()
        finally:
            self._remove_test_root()

    def _remove_test_root(self):
        """在严格校验目录归属后清理测试文件，防止误删工作区内容。"""
        tests_dir = Path(__file__).resolve().parent
        if self.test_root.parent != tests_dir or not self.test_root.name.startswith('.resume-management-'):
            raise AssertionError(f'unsafe test cleanup path: {self.test_root}')
        if not self.test_root.exists():
            return
        for path in sorted(self.test_root.rglob('*'), key=lambda item: len(item.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        self.test_root.rmdir()

    def _write_managed_resume(self, name: str, content: str) -> None:
        """直接写入隔离后的受管目录，用于构造测试前置状态。"""
        (self.resume_dir / name).write_text(content, encoding='utf-8')

    def test_web_selection_persists_and_drives_llm_resume(self):
        """网页选择应写入配置，并成为 LLM 后续读取的唯一当前简历。"""
        admin_store.save_resume('first.md', 'FIRST RESUME', select=True)
        admin_store.save_resume('selected.txt', '  WEB SELECTED RESUME  \n', select=False)

        result = admin_store.select_resume('selected.txt')

        self.assertEqual(result['selected'], 'selected.txt')
        self.assertEqual(result['configured'], 'selected.txt')
        self.assertEqual(
            json.loads(self.config_path.read_text(encoding='utf-8'))['resume_name'],
            'selected.txt',
        )

        config.Config.resume_name = 'first.md'
        config.Config.reload()
        self.assertEqual(config.Config.resume_name, 'selected.txt')
        self.assertEqual(core._load_resume(), 'WEB SELECTED RESUME')

    def test_save_without_selecting_keeps_current_resume(self):
        """保存草稿但不选择时，不应改变当前简历。"""
        admin_store.save_resume('current.md', 'CURRENT', select=True)
        admin_store.save_resume('draft.md', 'DRAFT', select=False)

        result = admin_store.list_resumes()

        self.assertEqual(result['selected'], 'current.md')
        self.assertEqual(config.Config.resume_name, 'current.md')
        self.assertEqual(core._load_resume(), 'CURRENT')

    def test_root_and_example_files_are_not_listed_or_loaded(self):
        """根目录旧文件和模板文件均不得进入管理列表或 LLM 上下文。"""
        (self.test_root / 'resume-example.md').write_text('ROOT TEMPLATE', encoding='utf-8')
        (self.test_root / 'resume.md').write_text('ROOT LEGACY RESUME', encoding='utf-8')
        self._write_managed_resume('managed.md', 'MANAGED RESUME')
        self._write_managed_resume('resume-template.md', 'MANAGED TEMPLATE')

        with patch.object(config.Config, 'resume_name', 'resume-example.md'):
            result = admin_store.list_resumes()
            llm_resume = core._load_resume()

        self.assertEqual([item['name'] for item in result['items']], ['managed.md'])
        self.assertEqual(result['selected'], 'managed.md')
        self.assertEqual(llm_resume, 'MANAGED RESUME')
        self.assertNotIn('ROOT TEMPLATE', llm_resume)
        self.assertNotIn('ROOT LEGACY RESUME', llm_resume)

    def test_rejects_path_traversal_bad_suffix_and_example_names(self):
        """拒绝路径穿越、不支持的后缀以及保留的示例命名。"""
        for name in ('../escape.md', r'..\escape.md', 'nested/resume.md'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '文件名无效'):
                resume_store.save_resume_file(name, 'content')

        with self.assertRaisesRegex(ValueError, r'只允许 \.md 或 \.txt'):
            resume_store.save_resume_file('resume.pdf', 'content')
        with self.assertRaisesRegex(ValueError, '示例或模板文件'):
            resume_store.save_resume_file('resume-example.md', 'content')

        self.assertFalse((self.test_root / 'escape.md').exists())

    def test_rejects_resume_larger_than_two_megabytes(self):
        """超限内容应在创建正式文件或临时文件前被拒绝。"""
        name = 'too-large.md'

        with self.assertRaisesRegex(ValueError, '不能超过 2MB'):
            resume_store.save_resume_file(name, 'x' * (resume_store.MAX_RESUME_SIZE + 1))

        self.assertFalse((self.resume_dir / name).exists())
        self.assertFalse((self.resume_dir / f'.{name}.tmp').exists())

    def test_new_resume_is_written_only_to_resume_directory(self):
        """新建简历只能落入专用目录，不能回写项目根目录。"""
        result = admin_store.save_resume('created.md', '# Created\n', select=False)

        self.assertEqual(result['name'], 'created.md')
        self.assertEqual((self.resume_dir / 'created.md').read_text(encoding='utf-8'), '# Created\n')
        self.assertFalse((self.test_root / 'created.md').exists())
        self.assertEqual(
            [item['name'] for item in admin_store.list_resumes()['items']],
            ['created.md'],
        )

    def test_validate_config_enforces_tag_content_uniqueness_and_limit(self):
        """关键词必须非空、忽略大小写后唯一，且最多保留 80 项。"""
        invalid_cases = [
            (['valid', '  '], '无效关键词'),
            (['AI Agent', ' ai agent '], '重复关键词'),
            ([f'tag-{index}' for index in range(81)], '1 到 80'),
        ]
        for tags, message in invalid_cases:
            with self.subTest(message=message):
                candidate = copy.deepcopy(config.DEFAULT_USER_CONFIG)
                candidate['tags'] = tags
                with self.assertRaisesRegex(ValueError, message):
                    admin_store.validate_config(candidate)

        maximum_size = copy.deepcopy(config.DEFAULT_USER_CONFIG)
        maximum_size['tags'] = [f'tag-{index}' for index in range(80)]
        admin_store.validate_config(maximum_size)


if __name__ == '__main__':
    unittest.main()
