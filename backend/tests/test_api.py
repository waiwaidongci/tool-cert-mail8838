import csv
import io
import os
import tempfile
import unittest

DB_FILE = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False).name
ARTIFACT_DIR = tempfile.mkdtemp(prefix='api-artifacts-')
os.environ.setdefault('DATABASE_URL', f'sqlite:///{DB_FILE}')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('SEED_DEMO', 'false')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')
os.environ.setdefault('ARTIFACT_DIR', ARTIFACT_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.domain.spec import SPEC  # noqa: E402
from src.main import app  # noqa: E402
from src.models.identity import User  # noqa: E402
from src.services import execution  # noqa: E402
from src.utils.security import hash_password  # noqa: E402


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)
        with Session.begin() as db:
            if not db.get(User, 'admin'):
                db.add(User(username='admin', role='admin', password_hash=hash_password('pw')))
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        token = self.client.post('/auth/login', json={'username': 'admin', 'password': 'pw'}).json()['token']
        self.headers = {'Authorization': f'Bearer {token}'}

    def upload(self):
        template = self.client.get('/template', headers=self.headers).text
        response = self.client.post('/datasets', headers=self.headers,
                                    json={'name': '演示批次', 'filename': 'demo.csv', 'csv_text': template})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def submit(self, dataset_id):
        response = self.client.post('/runs', headers=self.headers,
                                    json={'dataset_id': dataset_id, 'name': '班报', 'params': {}})
        self.assertEqual(response.status_code, 202)
        return response.json()['id']

    def test_current_report_until_first_run(self):
        self.client.get('/reports/current', headers=self.headers)

    def test_template_round_trip_and_line_report(self):
        dataset = self.upload()
        self.assertEqual(dataset['row_count'], 46)
        run_id = self.submit(dataset['id'])
        before = self.client.get('/reports/current', headers=self.headers).json()
        while True:
            lease = execution.claim()
            self.assertIsNotNone(lease)
            execution.execute(lease)
            if lease[0] == run_id:
                break
        detail = self.client.get(f'/runs/{run_id}', headers=self.headers).json()
        self.assertEqual(detail['status'], 'complete')
        self.assertEqual(detail['progress']['lines_done'], 4)
        report = self.client.get('/reports/current', headers=self.headers).json()
        self.assertEqual(report['dataset_id'], dataset['id'])
        if before is not None:
            self.assertNotEqual(report['run_id'], before['run_id'])
        lines = {line['line_code']: line for line in report['result']['lines']}
        self.assertEqual(lines['L-PACK']['status'], '已停线')
        self.assertEqual(lines['L-MACH']['combos'][0]['instruments'], ['QC-103'])
        export = self.client.get(f'/runs/{run_id}/export', headers=self.headers)
        self.assertEqual(export.status_code, 200)
        self.assertIn('产线最小停线组合', export.text)

    def test_upload_rejects_plan_without_line(self):
        template = self.client.get('/template', headers=self.headers).text
        row = {'record_type': '检验方案', 'line_code': 'L-NONE', 'plan_code': 'PX', 'plan_name': '无产线方案'}
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=[field['key'] for field in SPEC['fields']])
        writer.writerow(row)
        bad = template + buffer.getvalue()
        response = self.client.post('/datasets', headers=self.headers,
                                    json={'name': '坏批次', 'filename': 'bad.csv', 'csv_text': bad})
        self.assertEqual(response.status_code, 422)
        self.assertIn('L-NONE', response.json()['detail'])

    def test_export_before_completion_is_conflict(self):
        dataset = self.upload()
        run_id = self.submit(dataset['id'])
        response = self.client.get(f'/runs/{run_id}/export', headers=self.headers)
        self.assertEqual(response.status_code, 409)


if __name__ == '__main__':
    unittest.main()
