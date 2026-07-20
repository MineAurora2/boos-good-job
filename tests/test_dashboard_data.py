from __future__ import annotations

import json

from app.storage.dashboard_data import load_dashboard_data, parse_salary_details


class EmptyDeliveryStore:
    def status_indexes(self):
        return {}, {}


def test_parse_salary_details_uses_minimum_as_statistical_salary():
    assert parse_salary_details('5-8K·13薪') == {
        'salaryMinK': 5.0,
        'salaryMaxK': 8.0,
        'salaryK': 5.0,
        'salaryMonths': 13,
    }


def test_dashboard_average_salary_uses_minimum_salary(tmp_path):
    action_log = tmp_path / 'actions.jsonl'
    records = [
        {
            'action': 'delivery_claimed',
            'claimToken': 'a',
            'company': '甲公司',
            'title': '甲岗位',
            'salary': '5-8K',
            'loggedAt': '2026-07-21T09:00:00',
        },
        {
            'action': 'delivery_claimed',
            'claimToken': 'b',
            'company': '乙公司',
            'title': '乙岗位',
            'salary': '10-15K',
            'loggedAt': '2026-07-21T10:00:00',
        },
    ]
    action_log.write_text(
        '\n'.join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding='utf-8',
    )

    result = load_dashboard_data(action_log, EmptyDeliveryStore())

    assert result['summary']['averageSalaryK'] == 7.5
    assert [item['salaryK'] for item in result['deliveries']] == [10.0, 5.0]
