import pytest

from ..result_spool_logger import ResultSpoolLogger


class MockRecord:
    def __init__(self, msg):
        self.msg = msg
        self.exc_info = None
        self.exc_text = None
        self.stack_info = None

    def getMessage(self):
        return self.msg


@pytest.mark.django_db
class TestResultSpoolLogger:
    def test_emit(self, job_factory):
        job = job_factory(results={"test": [{"logs": ""}]}, org_id="00Dxxxxxxxxxxxxxxx")
        handler = ResultSpoolLogger(result=job)
        handler.current_key = "test"
        record = MockRecord("test")

        handler.emit(record)

        job.refresh_from_db()
        assert job.results == {"test": [{"logs": "\ntest"}]}

    def test_emit_none(self, job_factory):
        job = job_factory(results={}, org_id="00Dxxxxxxxxxxxxxxx")
        handler = ResultSpoolLogger(result=job)
        handler.current_key = None
        record = MockRecord("test")

        handler.emit(record)

        job.refresh_from_db()
        assert job.results == {}

    def test_emit_no_logs_key(self, job_factory):
        job = job_factory(results={"test": [{}]}, org_id="00Dxxxxxxxxxxxxxxx")
        handler = ResultSpoolLogger(result=job)
        handler.current_key = "test"
        record = MockRecord("test")

        handler.emit(record)

        job.refresh_from_db()
        assert job.results == {"test": [{"logs": "test"}]}

    def test_emit_no_current_key(self, job_factory):
        job = job_factory(results={}, org_id="00Dxxxxxxxxxxxxxxx")
        handler = ResultSpoolLogger(result=job)
        handler.current_key = "test"
        record = MockRecord("test")

        handler.emit(record)

        job.refresh_from_db()
        assert job.results == {"test": [{"logs": "test"}]}
