from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
from app.main import app
from app.services.billing_period import BEIJING,billing_period

class BillingTests(unittest.TestCase):
    def test_beijing_peak_boundaries_are_half_open(self):
        for clock,expected in [('08:59:59','off_peak'),('09:00:00','peak'),('11:59:59','peak'),('12:00:00','off_peak'),('13:59:59','off_peak'),('14:00:00','peak'),('17:59:59','peak'),('18:00:00','off_peak')]:
            with self.subTest(clock=clock):
                self.assertEqual(billing_period(datetime.fromisoformat('2026-11-09T'+clock).replace(tzinfo=BEIJING))['period'],expected)
    def test_utc_is_converted_and_boundary_refresh_is_short(self):
        result=billing_period(datetime(2026,11,9,0,59,59,tzinfo=timezone.utc))
        self.assertEqual(result['period'],'off_peak')
        self.assertLessEqual(result['refresh_after_seconds'],1.1)
        self.assertIn('+08:00',result['beijing_time'])
    def test_weekends_holidays_and_unconfigured_year_do_not_claim_peak(self):
        for date,expected in [('2026-11-07','off_peak'),('2026-02-17','off_peak'),('2026-10-05','off_peak'),('2027-01-04','unknown')]:
            self.assertEqual(billing_period(datetime.fromisoformat(date+'T10:00:00').replace(tzinfo=BEIJING))['period'],expected)
        # 周末即使调休上班，按公开计费规则仍不属于周一至周五。
        self.assertEqual(billing_period(datetime(2026,2,28,10,tzinfo=BEIJING))['period'],'off_peak')
    def test_endpoint_is_no_store_and_contains_no_key_or_model_call(self):
        with TestClient(app) as client:
            response=client.get('/api/billing/period')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIn(response.json()['period'],['peak','off_peak','unknown'])
            self.assertNotIn('api_key',response.text)
