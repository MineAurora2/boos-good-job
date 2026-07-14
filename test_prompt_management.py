import unittest

import prompts


class PromptManagementTest(unittest.TestCase):
    def test_required_placeholders_are_enforced(self):
        with self.assertRaises(ValueError):
            prompts.validate_prompt('CUSTOM_INTRODUCE', '只保留 {resume}')

    def test_unknown_placeholder_is_rejected(self):
        with self.assertRaises(ValueError):
            prompts.validate_prompt('CHAT', '{resume} {character} {unknown}')

    def test_valid_prompt_passes_format_validation(self):
        prompts.validate_prompt('JOB_FILTER', '简历：{resume}\n岗位：{job_info}')


if __name__ == '__main__':
    unittest.main()
