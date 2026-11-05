"""Scenario edits for tools/pyifc/sensorhub (pyifc). Applied to a scratch copy, one function per scenario.

Names: the new bucket is "sensorhub-archive", the T5 topic is "sensorhub-audit" with the worker "audit-parser",
T6 renames "sensorhub-staging" to "sensorhub-staging-v2". All edits are in app.py.
"""
from .lib import sub

RESULTS = 'results = Bucket("sensorhub-results")\n'
ARCHIVE = 'archive = Bucket("sensorhub-archive")\n'
INGEST_DECORATORS = "@app.write(staging)\n@app.publish(speed_readings)\n"
STAGING_PUT = "        staging.put(staging_key, body)\n"
QUERY_BODY = ") -> HttpResponse:\n    items = []\n"
QUERY_DECORATORS = "@app.list(results)\n@app.read(results)\n"
AUDIT_PUT = '    staging.put("query-audit/last.json", b"{}")\n'
AUDIT_TOPIC_ANCHOR = 'line_voltage_readings = Topic("sensorhub-line-voltage")\n'
AUDIT_WORKER = '''

@app.event_handler(id="audit-parser")
@app.subscribe(audit_readings)
def parse_audit(message: bytes) -> None:
    json.loads(message)
'''


def _archive_bucket(app):
    sub(app, "app.py", RESULTS, RESULTS + ARCHIVE)


def _archive_write_declaration(app):
    sub(app, "app.py", INGEST_DECORATORS, "@app.write(staging)\n@app.write(archive)\n@app.publish(speed_readings)\n")


def t0(app):
    pass


def t1(app):
    """Add a bucket `archive`; ingestion declares the write and makes one put."""
    _archive_bucket(app)
    _archive_write_declaration(app)
    sub(app, "app.py", STAGING_PUT, STAGING_PUT + "        archive.put(staging_key, body)\n")


def _query_writes_staging(app):
    sub(app, "app.py", QUERY_BODY, ") -> HttpResponse:\n" + AUDIT_PUT + "    items = []\n")


def t2(app):
    """Query puts an object into staging; no write is declared."""
    _query_writes_staging(app)


def t2b(app):
    """Query puts an object into staging; only `@app.read(staging)` is declared (read stays declared)."""
    _query_writes_staging(app)
    sub(app, "app.py", QUERY_DECORATORS, QUERY_DECORATORS + "@app.read(staging)\n")


def t3(app):
    """The bucket `archive` is declared; the write declaration and the put are dropped together."""
    _archive_bucket(app)


def t4(app):
    """`archive` keeps `@app.write(archive)`; no code writes to it."""
    _archive_bucket(app)
    _archive_write_declaration(app)


def t5(app):
    """Topic `sensorhub-audit` with the worker `audit-parser`; nothing publishes."""
    sub(app, "app.py", AUDIT_TOPIC_ANCHOR, AUDIT_TOPIC_ANCHOR + 'audit_readings = Topic("sensorhub-audit")\n')
    path = app / "app.py"
    path.write_text(path.read_text().rstrip("\n") + "\n" + AUDIT_WORKER)


def t6(app):
    """Rename the staging bucket to `sensorhub-staging-v2` (the one place that names it)."""
    sub(app, "app.py", '"sensorhub-staging"', '"sensorhub-staging-v2"')


EDITS = {"T0": t0, "T1": t1, "T2": t2, "T2b": t2b, "T3": t3, "T4": t4, "T5": t5, "T6": t6}
