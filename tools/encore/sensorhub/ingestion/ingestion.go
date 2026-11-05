// Package ingestion is the HTTP entry: POST /readings. It may write the
// staging bucket and publish to both topics, nothing else.
package ingestion

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"strings"

	"encore.dev/pubsub"
	"encore.dev/rlog"
	"encore.dev/storage/objects"

	"encore.app/reading"
	"encore.app/resources"
)

// Access of this service (inferred by Encore from these references).
var (
	staging         = objects.BucketRef[objects.Uploader](resources.Staging)
	speedJobs       = pubsub.TopicRef[pubsub.Publisher[*reading.Job]](resources.SpeedJobs)
	lineVoltageJobs = pubsub.TopicRef[pubsub.Publisher[*reading.Job]](resources.LineVoltageJobs)
)

//encore:api public raw method=POST path=/readings
func PostReadings(w http.ResponseWriter, req *http.Request) {
	body, err := io.ReadAll(io.LimitReader(req.Body, reading.MaxBodyBytes+1))
	status, code, message := checkRequest(req.Header.Get("Content-Type"), body, err)
	var kind reading.Kind
	if status == 0 {
		kind, err = reading.ValidateUpload(body)
		status, code, message = uploadError(err)
	}
	if status != 0 {
		writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
		return
	}

	id, err := newUUID()
	if err != nil {
		writeJSON(w, 503, errBody("staging_unavailable", "The reading could not be staged."))
		return
	}
	job := &reading.Job{ID: id, StagingKey: "raw/" + id + ".json"}
	if err := stage(req.Context(), job.StagingKey, body); err != nil {
		rlog.Error("staging write failed", "reading_id", id, "err", err)
		writeJSON(w, 503, errBody("staging_unavailable", "The reading could not be staged."))
		return
	}
	topic := speedJobs
	if kind == reading.LineVoltage {
		topic = lineVoltageJobs
	}
	if _, err := topic.Publish(req.Context(), job); err != nil {
		rlog.Error("publish failed", "reading_id", id, "staging_key", job.StagingKey, "err", err)
		writeJSON(w, 503, errBody("publishing_unavailable", "The reading was staged but could not be queued."))
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"id": id})
}

// checkRequest returns a zero status if the request may be validated further.
// The order follows the vanilla handler: media type, then size, then JSON.
func checkRequest(contentType string, body []byte, readErr error) (int, string, string) {
	mediaType, _, err := mime.ParseMediaType(contentType)
	switch {
	case err != nil || !strings.EqualFold(mediaType, "application/json"):
		return 415, "unsupported_media_type", "Content-Type must be application/json."
	case len(body) > reading.MaxBodyBytes:
		return 413, "payload_too_large", "The request body exceeds 4096 bytes."
	case readErr != nil:
		return 400, "invalid_request", "The request body is not valid JSON."
	}
	return 0, "", ""
}

// uploadError maps a validation error to status, code and message.
func uploadError(err error) (int, string, string) {
	switch {
	case err == nil:
		return 0, "", ""
	case errors.Is(err, reading.ErrInvalid):
		return 422, "invalid_reading", "The reading does not match the supported schema."
	default:
		return 400, "invalid_request", "The request body is not valid JSON."
	}
}

func stage(ctx context.Context, key string, body []byte) error {
	w := staging.Upload(ctx, key, objects.WithUploadAttrs(objects.UploadAttrs{ContentType: "application/json"}))
	if _, err := w.Write(body); err != nil {
		w.Abort(err)
		return err
	}
	return w.Close()
}

func newUUID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:]), nil
}

func errBody(code, message string) map[string]any {
	return map[string]any{"error": map[string]string{"code": code, "message": message}}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
