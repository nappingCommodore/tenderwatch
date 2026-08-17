"""Integration tests: parse the recorded HAR fixtures into the canonical DB.

These assert the pipeline reproduces known values from the captured portal
responses (tender 121339, its purchase order, documents, and corrigenda for
tender 1001).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bihar_ingestion.db import Database
from bihar_ingestion.parsers.corrigendum_parser import CorrigendumParser
from bihar_ingestion.parsers.masters_parser import MasterParser
from bihar_ingestion.parsers.po_parser import PurchaseOrderParser
from bihar_ingestion.parsers.tender_parser import TenderParser

FIXTURES = Path(__file__).parent / "fixtures" / "har"
BASE_URL = "https://eproc2.bihar.gov.in/EPSV2Web"


def _fixture(substring: str) -> dict | list:
    matches = sorted(FIXTURES.glob(f"*{substring}*.json"))
    if not matches:
        raise FileNotFoundError(f"No fixture matching {substring!r}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


class ParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.db.init_schema()
        self._seed_raw()

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _seed_raw(self) -> None:
        # Master + department payloads.
        self.db.archive_raw("masters", "master", "openareaMasterList",
                            _fixture("getMasterListForOpenareaTenderListing"))
        self.db.archive_raw("masters", "master", "rfqProcCatBidPart",
                            _fixture("showRfqCategoryRfqTypeProcCatBidPartList"))
        self.db.archive_raw("masters", "department", "org:538",
                            _fixture("loadAllDeptWithParent"))
        # Tender details.
        for tid in ("121339", "135242", "1001", "126359"):
            self.db.archive_raw("detail:preview", "tender", tid,
                                _fixture(f"previewTenderByTenderId_tenderId_{tid}"))
        # A discovery row so listing context propagates.
        self.db.execute(
            "INSERT INTO tender_discovery (tender_id, source_tab, listing_status, "
            "first_seen_at, last_seen_at) VALUES (121339, 'past', 'CLOSED', '', '')"
        )
        # Purchase order + corrigendum.
        self.db.archive_raw("detail:po", "po", "121339",
                            _fixture("getPoDetailsForPastTender_tenderId_121339"))
        self.db.archive_raw("detail:corrigendum", "corrigendum", "1001",
                            _fixture("getPublishedCorrigendumByTenderId_tenderId_1001"))
        self.db.commit()

    def _parse_all(self) -> None:
        MasterParser(self.db).run()
        TenderParser(self.db, BASE_URL).run()
        PurchaseOrderParser(self.db, BASE_URL).run()
        CorrigendumParser(self.db).run()
        self.db.rebuild_views()

    # -- dimensions --------------------------------------------------------
    def test_masters_loaded(self) -> None:
        MasterParser(self.db).run()
        self.assertEqual(
            self.db.scalar(
                "SELECT description FROM dim_procurement_category WHERE proc_cat_id = 1557"
            ),
            "CIVIL",
        )
        self.assertEqual(
            self.db.scalar("SELECT description FROM dim_rfq_type WHERE rfq_type_id = 101"),
            "Open Tender",
        )
        self.assertGreaterEqual(self.db.scalar("SELECT COUNT(*) FROM dim_department"), 100)
        self.assertGreater(self.db.scalar("SELECT COUNT(*) FROM dim_bid_part"), 0)

    # -- tenders + documents ----------------------------------------------
    def test_tender_parsed(self) -> None:
        self._parse_all()
        row = self.db.query("SELECT * FROM fact_tender WHERE tender_id = 121339")[0]
        self.assertEqual(row["ref_no"], "R2-17/2025-26/BUIDCO/BHAGALPUR/02")
        self.assertEqual(row["proc_cat_id"], 1557)
        self.assertAlmostEqual(row["pac_amount"], 340211.0, places=2)
        self.assertEqual(row["dept_path"], "538.1869.2254.2256.2273")
        self.assertEqual(row["source_tab"], "past")
        self.assertIsNotNone(row["bid_end_date"])
        self.assertIsNotNone(row["publish_date"])

    def test_documents_extracted(self) -> None:
        self._parse_all()
        docs = self.db.query(
            "SELECT * FROM fact_document WHERE tender_id = 121339 ORDER BY filename"
        )
        self.assertGreater(len(docs), 0)
        filenames = {d["filename"] for d in docs}
        self.assertTrue(any(f.lower().endswith(".pdf") for f in filenames))
        for d in docs:
            self.assertTrue(d["download_url"].startswith(BASE_URL))
            self.assertIn("relativePath=", d["download_url"])
            self.assertIsNone(d["sha256"])  # not downloaded in this phase

    # -- purchase order ----------------------------------------------------
    def test_purchase_order_parsed(self) -> None:
        self._parse_all()
        po = self.db.query("SELECT * FROM fact_purchase_order WHERE po_id = 62909")[0]
        self.assertEqual(po["tender_id"], 121339)
        self.assertEqual(po["vendor_id"], 76402)
        self.assertAlmostEqual(po["po_value"], 331705.72, places=2)
        self.assertEqual(po["currency"], "INR")

    def test_vendor_details_enriched(self) -> None:
        self._parse_all()
        v = self.db.query("SELECT * FROM dim_vendor WHERE vendor_id = 76402")[0]
        self.assertEqual(v["name"], "VIBHANSHU KUMAR")
        self.assertEqual(v["vendor_code"], "VIBHANSHU")
        self.assertEqual(v["uid_type"], "PAN")
        self.assertEqual(v["uid"], "EVXPK6790K")
        self.assertEqual(v["city"], "BHAGALPUR")
        self.assertEqual(v["state"], "Bihar")
        # And the enriched name flows through to the analytics view.
        award = self.db.query("SELECT * FROM v_award_summary WHERE tender_id = 121339")[0]
        self.assertEqual(award["vendor_name"], "VIBHANSHU KUMAR")

    def test_po_line_items_parsed(self) -> None:
        self._parse_all()
        items = self.db.query(
            "SELECT * FROM fact_po_item WHERE po_id = 62909 ORDER BY serial_no"
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["tender_id"], 121339)
        self.assertEqual(item["serial_no"], 1)
        self.assertEqual(item["item_code"], "R2-17/2025-26/BUIDCO/BHAGALPUR/02")
        self.assertIn("Construction work", item["item_name"])
        self.assertAlmostEqual(item["unit_price_rate"], 331705.72, places=2)
        self.assertAlmostEqual(item["total_cost"], 331705.72, places=2)
        # Line item is exposed via the analytics view with vendor context.
        vrow = self.db.query("SELECT * FROM v_po_line_items WHERE po_id = 62909")[0]
        self.assertEqual(vrow["vendor_name"], "VIBHANSHU KUMAR")

    # -- corrigenda --------------------------------------------------------
    def test_corrigenda_parsed(self) -> None:
        self._parse_all()
        rows = self.db.query("SELECT * FROM fact_corrigendum WHERE tender_id = 1001")
        self.assertEqual(len(rows), 2)
        versions = sorted(r["version_no"] for r in rows)
        self.assertEqual(versions, [1, 2])

    # -- integrity + analytics --------------------------------------------
    def test_foreign_keys_and_views(self) -> None:
        self._parse_all()
        self.assertEqual(len(self.db.query("PRAGMA foreign_key_check")), 0)
        award = self.db.query("SELECT * FROM v_award_summary WHERE tender_id = 121339")
        self.assertEqual(len(award), 1)
        self.assertAlmostEqual(award[0]["po_value"], 331705.72, places=2)

    def test_idempotent_reparse(self) -> None:
        self._parse_all()
        before = self.db.scalar("SELECT COUNT(*) FROM fact_document")
        before_items = self.db.scalar("SELECT COUNT(*) FROM fact_po_item")
        self._parse_all()  # run again
        after = self.db.scalar("SELECT COUNT(*) FROM fact_document")
        after_items = self.db.scalar("SELECT COUNT(*) FROM fact_po_item")
        self.assertEqual(before, after)
        self.assertEqual(before_items, after_items)
        self.assertEqual(self.db.scalar("SELECT COUNT(*) FROM fact_tender"), 4)


if __name__ == "__main__":
    unittest.main()
