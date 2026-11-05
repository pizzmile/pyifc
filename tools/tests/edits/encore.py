"""Scenario edits for tools/encore/sensorhub (Go). Applied to a scratch copy, one function per scenario.

Names: the new bucket is "sensorhub-archive", the T5 topic is "sensorhub-audit", T6 renames
"sensorhub-staging" to "sensorhub-staging-v2" (the Go variable `Staging` stays).
"""
from .lib import add_file, sub

RESULTS_DECL = 'Results = objects.NewBucket("sensorhub-results", objects.BucketConfig{})\n'
ARCHIVE_DECL = '\tArchive = objects.NewBucket("sensorhub-archive", objects.BucketConfig{})\n'
STAGING_REF = "\tstaging         = objects.BucketRef[objects.Uploader](resources.Staging)\n"
ARCHIVE_REF = "\tarchive         = objects.BucketRef[objects.Uploader](resources.Archive)\n"
TOPIC_ANCHOR = "\ttopic := speedJobs\n"
ARCHIVE_CALL = '''	if err := archiveReading(req.Context(), job.StagingKey, body); err != nil {
		writeJSON(w, 503, errBody("archive_unavailable", "The reading could not be archived."))
		return
	}
'''
ARCHIVE_FUNC = '''func archiveReading(ctx context.Context, key string, body []byte) error {
	w := archive.Upload(ctx, key, objects.WithUploadAttrs(objects.UploadAttrs{ContentType: "application/json"}))
	if _, err := w.Write(body); err != nil {
		w.Abort(err)
		return err
	}
	return w.Close()
}

'''
AUDIT_PKG = '''// Package audit consumes the audit topic. Nothing publishes to it.
package audit

import (
	"context"

	"encore.dev/pubsub"

	"encore.app/reading"
	"encore.app/resources"
)

var _ = pubsub.NewSubscription(resources.AuditJobs, "audit-writer", pubsub.SubscriptionConfig[*reading.Job]{
	Handler: handle,
})

func handle(ctx context.Context, job *reading.Job) error { return nil }
'''


def _declare_archive(app):
    sub(app, "resources/resources.go", RESULTS_DECL, RESULTS_DECL + ARCHIVE_DECL)


def t0(app):
    pass


def t1(app):
    """Add a bucket `archive`; ingestion writes to it."""
    _declare_archive(app)
    sub(app, "ingestion/ingestion.go", STAGING_REF, STAGING_REF + ARCHIVE_REF)
    sub(app, "ingestion/ingestion.go", TOPIC_ANCHOR, ARCHIVE_CALL + TOPIC_ANCHOR)
    sub(app, "ingestion/ingestion.go", "func newUUID() (string, error) {", ARCHIVE_FUNC + "func newUUID() (string, error) {")


def t2(app):
    """Query uploads to staging, which it did not use before. Like the Nitric edit, a failed write fails the request."""
    sub(app, "query/query.go", "var results = objects.BucketRef[resultsReader](resources.Results)\n",
        "var results = objects.BucketRef[resultsReader](resources.Results)\n"
        "var staging = objects.BucketRef[objects.Uploader](resources.Staging)\n")
    sub(app, "query/query.go", "\tlimit, fleetID, assetID, err := parseQuery(req)\n",
        '''	if err := recordQuery(req); err != nil {
		rlog.Error("query audit failed", "err", err)
		writeJSON(w, 503, errBody("audit_unavailable", "The query could not be recorded."))
		return
	}
	limit, fleetID, assetID, err := parseQuery(req)
''')
    sub(app, "query/query.go", "// parseQuery applies the SPEC rules",
        '''func recordQuery(req *http.Request) error {
	w := staging.Upload(req.Context(), "query-audit/last.json", objects.WithUploadAttrs(objects.UploadAttrs{ContentType: "application/json"}))
	if _, err := w.Write([]byte(`{}`)); err != nil {
		w.Abort(err)
		return err
	}
	return w.Close()
}

// parseQuery applies the SPEC rules''')


def t2b(app):
    """Same edit as T2: Encore has no read-only handle to write through."""
    t2(app)


def t3(app):
    """The bucket `archive` is declared; nothing references it."""
    _declare_archive(app)


def t4(app):
    """`archive` is declared and ingestion keeps an Uploader reference that nothing uses."""
    _declare_archive(app)
    sub(app, "ingestion/ingestion.go", STAGING_REF, STAGING_REF + ARCHIVE_REF)


def t5(app):
    """Topic `audit` with a subscription in a new package; nothing publishes."""
    sub(app, "resources/resources.go", "// Topics: one per kind.",
        '''// Audit topic: has a subscriber and no publisher.
var AuditJobs = pubsub.NewTopic[*reading.Job]("sensorhub-audit", pubsub.TopicConfig{DeliveryGuarantee: pubsub.AtLeastOnce})

// Topics: one per kind.''')
    add_file(app, "audit/audit.go", AUDIT_PKG)


def t6(app):
    """Rename the staging bucket's name to `sensorhub-staging-v2`."""
    sub(app, "resources/resources.go", '"sensorhub-staging"', '"sensorhub-staging-v2"')


EDITS = {"T0": t0, "T1": t1, "T2": t2, "T2b": t2b, "T3": t3, "T4": t4, "T5": t5, "T6": t6}
