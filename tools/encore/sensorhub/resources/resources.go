// Package resources declares every bucket and topic of the sensor hub in one
// place. It has no API, so it is not a service; each service reaches the
// resources it needs through a typed reference (BucketRef / TopicRef), and
// Encore derives that service's access from the reference's permission type.
package resources

import (
	"encore.dev/pubsub"
	"encore.dev/storage/objects"

	"encore.app/reading"
)

// Buckets: raw uploads and normalized results.
var (
	Staging = objects.NewBucket("sensorhub-staging", objects.BucketConfig{})
	Results = objects.NewBucket("sensorhub-results", objects.BucketConfig{})
)

// Topics: one per kind. Encore has no raw queue; a topic with one subscription
// is the closest equivalent.
var (
	SpeedJobs       = pubsub.NewTopic[*reading.Job]("sensorhub-speed", pubsub.TopicConfig{DeliveryGuarantee: pubsub.AtLeastOnce})
	LineVoltageJobs = pubsub.NewTopic[*reading.Job]("sensorhub-line-voltage", pubsub.TopicConfig{DeliveryGuarantee: pubsub.AtLeastOnce})
)
