// Package query serves GET /readings. It may only list and read the results
// bucket.
package query

import (
	"cmp"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"slices"
	"strconv"
	"strings"

	"encore.dev/rlog"
	"encore.dev/storage/objects"

	"encore.app/reading"
	"encore.app/resources"
)

type resultsReader interface {
	objects.Lister
	objects.Downloader
}

var results = objects.BucketRef[resultsReader](resources.Results)

const (
	defaultLimit = 50
	maxLimit     = 100
)

var (
	errLimit  = errors.New("invalid limit")
	errFilter = errors.New("invalid train filter")
)

//encore:api public raw method=GET path=/readings
func GetReadings(w http.ResponseWriter, req *http.Request) {
	limit, fleetID, assetID, err := parseQuery(req)
	if err != nil {
		code, message := "invalid_limit", "The limit query parameter must be an integer."
		if errors.Is(err, errFilter) {
			code, message = "invalid_train_filter", "fleet_id and asset_id must be provided together and must not be empty."
		}
		writeJSON(w, 400, errBody(code, message))
		return
	}
	var items []reading.Processed
	for entry, err := range results.List(req.Context(), &objects.Query{}) {
		if err != nil {
			rlog.Error("list results failed", "err", err)
			writeJSON(w, 503, errBody("results_unavailable", "The processed readings could not be retrieved."))
			return
		}
		body, err := io.ReadAll(results.Download(req.Context(), entry.Name))
		if err != nil {
			rlog.Error("read result failed", "key", entry.Name, "err", err)
			writeJSON(w, 503, errBody("results_unavailable", "The processed readings could not be retrieved."))
			return
		}
		var item reading.Processed
		if json.Unmarshal(body, &item) != nil {
			continue // skip objects that are not processed records
		}
		items = append(items, item)
	}
	writeJSON(w, 200, map[string]any{"readings": Select(items, limit, fleetID, assetID)})
}

// parseQuery applies the SPEC rules: limit is an integer (negative counts as
// 0, capped at 100); fleet_id and asset_id come together and are non-empty.
func parseQuery(req *http.Request) (limit int, fleetID, assetID string, err error) {
	q := req.URL.Query()
	limit = defaultLimit
	if raw := q.Get("limit"); raw != "" {
		n, convErr := strconv.Atoi(strings.TrimSpace(raw))
		if convErr != nil {
			return 0, "", "", errLimit
		}
		limit = min(max(n, 0), maxLimit)
	}
	fleetID, assetID = q.Get("fleet_id"), q.Get("asset_id")
	if q.Has("fleet_id") != q.Has("asset_id") ||
		(q.Has("fleet_id") && (strings.TrimSpace(fleetID) == "" || strings.TrimSpace(assetID) == "")) {
		return 0, "", "", errFilter
	}
	return limit, fleetID, assetID, nil
}

// Select filters by train (when fleetID is set), sorts newest first by
// timestamp then ID, and cuts to limit.
func Select(items []reading.Processed, limit int, fleetID, assetID string) []reading.Processed {
	out := make([]reading.Processed, 0, len(items))
	for _, it := range items {
		if fleetID == "" || (it.FleetID == fleetID && it.AssetID == assetID) {
			out = append(out, it)
		}
	}
	slices.SortFunc(out, func(a, b reading.Processed) int {
		return cmp.Or(cmp.Compare(b.Timestamp, a.Timestamp), cmp.Compare(b.ID, a.ID))
	})
	return out[:min(limit, len(out))]
}

func errBody(code, message string) map[string]any {
	return map[string]any{"error": map[string]string{"code": code, "message": message}}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
