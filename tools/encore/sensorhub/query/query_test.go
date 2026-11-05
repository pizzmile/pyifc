package query

import (
	"net/http/httptest"
	"strconv"
	"testing"

	"encore.app/reading"
)

func TestParseQuery(t *testing.T) {
	tests := []struct {
		url     string
		limit   int
		wantErr error
	}{
		{"/readings", 50, nil},
		{"/readings?limit=7", 7, nil},
		{"/readings?limit=1000", 100, nil},
		{"/readings?limit=-3", 0, nil},
		{"/readings?limit=abc", 0, errLimit},
		{"/readings?fleet_id=f&asset_id=t", 50, nil},
		{"/readings?fleet_id=f", 0, errFilter},
		{"/readings?asset_id=t", 0, errFilter},
		{"/readings?fleet_id=&asset_id=t", 0, errFilter},
		{"/readings?fleet_id=%20&asset_id=t", 0, errFilter},
	}
	for _, tc := range tests {
		t.Run(tc.url, func(t *testing.T) {
			limit, _, _, err := parseQuery(httptest.NewRequest("GET", tc.url, nil))
			if err != tc.wantErr || limit != tc.limit {
				t.Fatalf("limit=%d err=%v, want limit=%d err=%v", limit, err, tc.limit, tc.wantErr)
			}
		})
	}
}

func TestSelectOrdersNewestFirstAndBreaksTiesByID(t *testing.T) {
	items := []reading.Processed{
		{ID: "a", Timestamp: "2026-09-23T10:00:00Z", FleetID: "f", AssetID: "t1"},
		{ID: "b", Timestamp: "2026-09-23T11:00:00Z", FleetID: "f", AssetID: "t1"},
		{ID: "c", Timestamp: "2026-09-23T11:00:00Z", FleetID: "f", AssetID: "t2"},
	}
	var got []string
	for _, r := range Select(items, 50, "", "") {
		got = append(got, r.ID)
	}
	if want := "c,b,a"; join(got) != want {
		t.Fatalf("order = %s, want %s", join(got), want)
	}
	if got := Select(items, 50, "f", "t1"); len(got) != 2 || got[0].ID != "b" {
		t.Fatalf("filter = %+v", got)
	}
	if got := Select(items, 1, "", ""); len(got) != 1 || got[0].ID != "c" {
		t.Fatalf("limit = %+v", got)
	}
	if got := Select(items, 0, "", ""); got == nil || len(got) != 0 {
		t.Fatalf("limit 0 must give an empty, non-nil list, got %#v", got)
	}
}

func TestSelectCapsAtOneHundredWhenGivenParsedLimit(t *testing.T) {
	var items []reading.Processed
	for i := range 120 {
		items = append(items, reading.Processed{ID: strconv.Itoa(1000 + i)})
	}
	limit, _, _, _ := parseQuery(httptest.NewRequest("GET", "/readings?limit=500", nil))
	if got := len(Select(items, limit, "", "")); got != 100 {
		t.Fatalf("got %d readings, want 100", got)
	}
}

func join(s []string) string {
	out := ""
	for i, v := range s {
		if i > 0 {
			out += ","
		}
		out += v
	}
	return out
}
