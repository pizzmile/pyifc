// Package speedparser consumes only the speed topic. It may read the staging
// bucket and write the results bucket.
package speedparser

import (
	"context"
	"time"

	"encore.dev/pubsub"
	"encore.dev/storage/objects"

	"encore.app/reading"
	"encore.app/resources"
)

var (
	staging = objects.BucketRef[objects.Downloader](resources.Staging)
	results = objects.BucketRef[objects.Uploader](resources.Results)
)

var _ = pubsub.NewSubscription(resources.SpeedJobs, "speed-parser", pubsub.SubscriptionConfig[*reading.Job]{
	Handler:     handle,
	RetryPolicy: &pubsub.RetryPolicy{MaxRetries: 3, MinBackoff: time.Second, MaxBackoff: 5 * time.Second},
})

func handle(ctx context.Context, job *reading.Job) error {
	return reading.Speed.Process(ctx, job, staging, results)
}
