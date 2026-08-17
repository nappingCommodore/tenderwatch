"""Unit tests for the pure helpers in bihar_ingestion.utils."""

from __future__ import annotations

import unittest

from bihar_ingestion.utils import (
    epoch_ms_to_iso,
    parse_attachment_value,
    parse_dept_path,
    sane_epoch,
)


class UtilsTest(unittest.TestCase):
    def test_epoch_conversion(self) -> None:
        # 1766757047000 -> 2025-12-26T ... UTC
        iso = epoch_ms_to_iso(1766757047000)
        self.assertIsNotNone(iso)
        self.assertTrue(iso.startswith("2025-12-26"))

    def test_epoch_rejects_sentinels(self) -> None:
        self.assertIsNone(epoch_ms_to_iso(0))
        self.assertIsNone(sane_epoch(None))
        self.assertIsNone(sane_epoch(""))

    def test_attachment_two_parts(self) -> None:
        parsed = parse_attachment_value("29679/1766756175102_NIT 17.pdf|NIT 17.pdf")
        self.assertEqual(parsed["relative_path"], "29679/1766756175102_NIT 17.pdf")
        self.assertEqual(parsed["filename"], "NIT 17.pdf")
        self.assertIsNone(parsed["file_size_bytes"])
        self.assertEqual(parsed["template_group_id"], 29679)
        self.assertEqual(parsed["mime_type"], "application/pdf")

    def test_attachment_three_parts_with_size(self) -> None:
        parsed = parse_attachment_value("29692/1766756627574_NIT 17.pdf|8060784|NIT 17.pdf")
        self.assertEqual(parsed["file_size_bytes"], 8060784)
        self.assertEqual(parsed["filename"], "NIT 17.pdf")

    def test_attachment_xlsx(self) -> None:
        parsed = parse_attachment_value(
            "29692/1766756615372_BOQ NIT 17 .xlsx|74254|BOQ NIT 17 .xlsx"
        )
        self.assertEqual(
            parsed["mime_type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_attachment_rejects_non_attachments(self) -> None:
        self.assertIsNone(parse_attachment_value("NIT 17.pdf"))
        self.assertIsNone(parse_attachment_value("INR"))
        self.assertIsNone(parse_attachment_value(None))
        self.assertIsNone(parse_attachment_value(123))

    def test_dept_path(self) -> None:
        self.assertEqual(
            parse_dept_path("538.1869.2254.2256.2273."),
            [538, 1869, 2254, 2256, 2273],
        )
        self.assertEqual(parse_dept_path(None), [])


if __name__ == "__main__":
    unittest.main()
