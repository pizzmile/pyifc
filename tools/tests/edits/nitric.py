"""Scenario edits for tools/nitric/sensorhub (Python). Applied to a scratch copy, one function per scenario.

Names: the new bucket is "archive", the T5 topic is "audit-events", T6 renames "staging" to "staging-v2".
"""
from .lib import add_file, sub

ARCHIVE_WRITE = '''

def archive_write():
    return bucket("archive").allow("write")
'''
ARCHIVE_PLAIN = '''

def archive_bucket():
    return bucket("archive")
'''
AUDIT_SERVICE = '''from nitric.application import Nitric

from resources import audit_topic


@audit_topic().subscribe()
async def audit_event(ctx):
    print("audit event", ctx.req.data)


Nitric.run()
'''
IMPORT_OLD = """from resources import (
    line_voltage_publish,
    sensorhub_api,
    speed_publish,
    staging_write,
)
"""


def _resources(app, snippet):
    path = app / "resources.py"
    path.write_text(path.read_text().rstrip("\n") + "\n" + snippet)


def t0(app):
    pass


def t1(app):
    """Add a bucket `archive`; ingestion writes to it."""
    _resources(app, ARCHIVE_WRITE)
    sub(app, "handlers.py", "async def ingest(ctx, staging, publishers):",
        "async def ingest(ctx, staging, publishers, archive):")
    sub(app, "handlers.py", "    await staging.file(staging_key).write(body)\n",
        "    await staging.file(staging_key).write(body)\n    await archive.file(staging_key).write(body)\n")
    sub(app, "services/ingestion.py", IMPORT_OLD, IMPORT_OLD.replace("from resources import (\n", "from resources import (\n    archive_write,\n"))
    sub(app, "services/ingestion.py", "staging = staging_write()\n", "staging = staging_write()\narchive = archive_write()\n")
    sub(app, "services/ingestion.py", "return await ingest(ctx, staging, publishers)",
        "return await ingest(ctx, staging, publishers, archive)")


def _query_writes_staging(app, handle):
    sub(app, "services/query.py", "from nitric.application import Nitric\n",
        "from nitric.application import Nitric\nfrom nitric.resources import bucket\n")
    sub(app, "services/query.py", "results = results_read()\n", f"results = results_read()\nstaging = {handle}\n")
    sub(app, "services/query.py", "    return await query(ctx, results)",
        '    await staging.file("query-audit/last.json").write(b"{}")\n    return await query(ctx, results)')


def t2(app):
    """Query writes to staging through a handle created without `.allow()`."""
    _query_writes_staging(app, 'bucket("staging")')


def t2b(app):
    """Query writes to staging through a handle declared `.allow("read")`."""
    _query_writes_staging(app, 'bucket("staging").allow("read")')


def t3(app):
    """The bucket `archive` is declared, nothing writes to it and nothing allows it."""
    _resources(app, ARCHIVE_PLAIN)
    sub(app, "services/ingestion.py", IMPORT_OLD, IMPORT_OLD.replace("from resources import (\n", "from resources import (\n    archive_bucket,\n"))
    sub(app, "services/ingestion.py", "staging = staging_write()\n", "staging = staging_write()\narchive = archive_bucket()\n")


def t4(app):
    """`archive` keeps `.allow("write")`; no code writes to it."""
    _resources(app, ARCHIVE_WRITE)
    sub(app, "services/ingestion.py", IMPORT_OLD, IMPORT_OLD.replace("from resources import (\n", "from resources import (\n    archive_write,\n"))
    sub(app, "services/ingestion.py", "staging = staging_write()\n", "staging = staging_write()\narchive = archive_write()\n")


def t5(app):
    """Topic `audit-events` with a subscriber service; nothing publishes."""
    _resources(app, '\n\ndef audit_topic():\n    return topic("audit-events")\n')
    add_file(app, "services/audit.py", AUDIT_SERVICE)


def t6(app):
    """Rename the staging bucket to `staging-v2` (two helpers name it)."""
    sub(app, "resources.py", 'bucket("staging")', 'bucket("staging-v2")', count=2)


EDITS = {"T0": t0, "T1": t1, "T2": t2, "T2b": t2b, "T3": t3, "T4": t4, "T5": t5, "T6": t6}
