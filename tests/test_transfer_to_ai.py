from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from transfer_to_ai import TransferConfig, build_plan, normalize_header, previous_workday


class TransferToAiTest(unittest.TestCase):
    def setUp(self):
        self.config = TransferConfig(
            spreadsheet_id="sheet",
            credentials_file="credentials.json",
            source_sheet="Данные",
            destination_sheet="Данные для обзвона AI",
            start_date=date(2026, 8, 27),
            not_called_workdays=3,
            no_answer_workdays=4,
            no_answer_statuses=frozenset(
                normalize_header(value)
                for value in ("Недозвон", "Автоответчик / помощник", "Сброс / молчит", "Не удалось связаться 9 раз")
            ),
            source_transferred_status="Передали AI — статус не менять",
            destination_not_called_status="Передали AI — менеджеры не звонили",
            destination_no_answer_status="Передали AI — недозвон",
            headers={
                "source_id": "ID",
                "date": "Дата",
                "phone": "Номера",
                "channel": "Канал",
                "source": "Источник",
                "status": "Статус (позвонил - выбери нужный)",
            },
        )

    def test_monday_cutoffs_and_status_mapping(self):
        today = date(2026, 9, 14)
        self.assertEqual(previous_workday(today, 3), date(2026, 9, 9))
        self.assertEqual(previous_workday(today, 4), date(2026, 9, 8))
        rows = [
            ["ID", "Дата", "Номера", "Канал", "Источник", "Статус (позвонил - выбери нужный)"],
            ["1", "2026-09-09 10:00:00", "+70000000001", "A", "S", ""],
            ["2", "2026-09-08 10:00:00", "+70000000002", "A", "S", "Недозвон"],
            ["3", "2026-09-09 10:00:00", "+70000000003", "A", "S", "Недозвон"],
            ["4", "2026-09-08 10:00:00", "+70000000004", "A", "S", "ЛИД"],
        ]
        plan = build_plan(self.config, rows, {"2"}, today=today)
        self.assertEqual(plan.not_called, 1)
        self.assertEqual(plan.no_answer, 1)
        self.assertEqual(plan.too_recent, 1)
        self.assertEqual(plan.other_status, 1)
        self.assertEqual(plan.already_in_destination, 1)
        self.assertEqual(plan.transfers[0].values[-1], "Передали AI — менеджеры не звонили")
        self.assertEqual(plan.transfers[1].values[-1], "Передали AI — недозвон")


if __name__ == "__main__":
    unittest.main()
