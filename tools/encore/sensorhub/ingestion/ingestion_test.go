package ingestion

import (
	"errors"
	"testing"

	"encore.app/reading"
)

func TestCheckRequest(t *testing.T) {
	big := make([]byte, reading.MaxBodyBytes+1)
	ok := make([]byte, reading.MaxBodyBytes)
	tests := []struct {
		name        string
		contentType string
		body        []byte
		readErr     error
		want        int
	}{
		{"json", "application/json", []byte("{}"), nil, 0},
		{"json with charset", "application/json; charset=utf-8", []byte("{}"), nil, 0},
		{"exactly 4 KiB", "application/json", ok, nil, 0},
		{"one byte over", "application/json", big, nil, 413},
		{"text", "text/plain", []byte("{}"), nil, 415},
		{"no content type", "", []byte("{}"), nil, 415},
		{"read error", "application/json", nil, errors.New("boom"), 400},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got, _, _ := checkRequest(tc.contentType, tc.body, tc.readErr); got != tc.want {
				t.Fatalf("status = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestUploadErrorStatus(t *testing.T) {
	for err, want := range map[error]int{nil: 0, reading.ErrMalformed: 400, reading.ErrInvalid: 422} {
		if got, _, _ := uploadError(err); got != want {
			t.Errorf("uploadError(%v) = %d, want %d", err, got, want)
		}
	}
}

func TestNewUUID(t *testing.T) {
	id, err := newUUID()
	if err != nil {
		t.Fatal(err)
	}
	if err := reading.ValidateJob(reading.Job{ID: id, StagingKey: "k"}); err != nil {
		t.Fatalf("generated ID %q fails the job schema: %v", id, err)
	}
}
