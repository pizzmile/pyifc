package reading

import (
	"context"
	"fmt"
	"io"

	"encore.dev/storage/objects"
)

// Process is the body of both parsers. The caller passes in the bucket
// references it owns, so the access stays visible in the parser's own package.
// A failure is returned (never reported as processed), so Pub/Sub retries it.
func (k Kind) Process(ctx context.Context, job *Job, staging objects.Downloader, results objects.Uploader) error {
	if job == nil {
		return fmt.Errorf("%w: empty job", ErrInvalid)
	}
	r := staging.Download(ctx, job.StagingKey)
	staged, err := io.ReadAll(r)
	if err != nil {
		return fmt.Errorf("read staged object %q: %w", job.StagingKey, err)
	}
	record, err := k.Normalize(*job, staged)
	if err != nil {
		return fmt.Errorf("%s parser, reading %s: %w", k.Name, job.ID, err)
	}
	// The key is the reading ID, so a retry replaces the same result.
	w := results.Upload(ctx, job.ID+".json", objects.WithUploadAttrs(objects.UploadAttrs{ContentType: "application/json"}))
	if _, err := w.Write(record); err != nil {
		w.Abort(err)
		return fmt.Errorf("write result: %w", err)
	}
	return w.Close()
}
