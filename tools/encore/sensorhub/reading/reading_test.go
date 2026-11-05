package reading

import (
	"errors"
	"strings"
	"testing"
)

const id = "123e4567-e89b-12d3-a456-426614174000"

func speed(ts, value string) string {
	return `{"kind":"speed","fleet_id":"f","asset_id":"t","timestamp":"` + ts + `","value":` + value + `}`
}

func TestValidateUpload(t *testing.T) {
	long := strings.Repeat("x", 129)
	tests := []struct {
		name string
		body string
		kind string
		err  error
	}{
		{"speed ok", speed("2026-09-23T10:00:00Z", "132.6"), "speed", nil},
		{"zero ok", speed("2026-09-23T10:00:00Z", "0"), "speed", nil},
		{"voltage ok", `{"kind":"line_voltage","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":26680}`, "line_voltage", nil},
		{"not json", `{"kind":`, "", ErrMalformed},
		{"json array", `[1]`, "", ErrMalformed},
		{"NaN token", speed("2026-09-23T10:00:00Z", "NaN"), "", ErrMalformed},
		{"Infinity token", speed("2026-09-23T10:00:00Z", "Infinity"), "", ErrMalformed},
		{"invalid utf-8", "{\"kind\":\"\xff\"}", "", ErrMalformed},
		{"unknown kind", `{"kind":"temperature","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":1}`, "", ErrInvalid},
		{"missing kind", `{"fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":1}`, "", ErrInvalid},
		{"missing value", `{"kind":"speed","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z"}`, "", ErrInvalid},
		{"extra field", `{"kind":"speed","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":1,"x":1}`, "", ErrInvalid},
		{"string value", speed("2026-09-23T10:00:00Z", `"fast"`), "", ErrInvalid},
		{"negative value", speed("2026-09-23T10:00:00Z", "-1"), "", ErrInvalid},
		{"overflow value", speed("2026-09-23T10:00:00Z", "1e999"), "", ErrInvalid},
		{"empty fleet", `{"kind":"speed","fleet_id":"","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":1}`, "", ErrInvalid},
		{"long asset", `{"kind":"speed","fleet_id":"f","asset_id":"` + long + `","timestamp":"2026-09-23T10:00:00Z","value":1}`, "", ErrInvalid},
		{"offset timestamp", speed("2026-09-23T10:00:00+00:00", "1"), "", ErrInvalid},
		{"impossible date", speed("2026-02-30T10:00:00Z", "1"), "", ErrInvalid},
		{"not a timestamp", speed("yesterday", "1"), "", ErrInvalid},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			kind, err := ValidateUpload([]byte(tc.body))
			if !errors.Is(err, tc.err) || (tc.err == nil && err != nil) {
				t.Fatalf("err = %v, want %v", err, tc.err)
			}
			if kind.Name != tc.kind {
				t.Fatalf("kind = %q, want %q", kind.Name, tc.kind)
			}
		})
	}
}

func TestNormalize(t *testing.T) {
	job := Job{ID: id, StagingKey: "raw/" + id + ".json"}
	tests := []struct {
		name   string
		kind   Kind
		staged string
		want   string
	}{
		{"moving", Speed, speed("2026-09-23T10:00:00Z", "132.6"),
			`{"id":"` + id + `","kind":"speed","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":132.6,"unit":"km/h","status":"moving"}`},
		{"stopped", Speed, speed("2026-09-23T10:00:00Z", "0"),
			`{"id":"` + id + `","kind":"speed","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":0,"unit":"km/h","status":"stopped"}`},
		{"voltage", LineVoltage, `{"kind":"line_voltage","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":0}`,
			`{"id":"` + id + `","kind":"line_voltage","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":0,"unit":"V","status":"no_voltage_observed"}`},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := tc.kind.Normalize(job, []byte(tc.staged))
			if err != nil {
				t.Fatal(err)
			}
			if string(got) != tc.want {
				t.Fatalf("got  %s\nwant %s", got, tc.want)
			}
		})
	}
}

func TestParserRejectsOtherKindsAndBadInput(t *testing.T) {
	job := Job{ID: id, StagingKey: "raw/x.json"}
	volt := `{"kind":"line_voltage","fleet_id":"f","asset_id":"t","timestamp":"2026-09-23T10:00:00Z","value":1}`
	if _, err := Speed.Normalize(job, []byte(volt)); !errors.Is(err, ErrInvalid) {
		t.Errorf("speed parser accepted a line_voltage reading: %v", err)
	}
	if _, err := LineVoltage.Normalize(job, []byte(speed("2026-09-23T10:00:00Z", "1"))); !errors.Is(err, ErrInvalid) {
		t.Errorf("line-voltage parser accepted a speed reading: %v", err)
	}
	if _, err := Speed.Normalize(job, []byte("not json")); !errors.Is(err, ErrMalformed) {
		t.Errorf("malformed staged object: %v", err)
	}
	if _, err := Speed.Normalize(Job{ID: "not-a-uuid", StagingKey: "k"}, []byte(speed("2026-09-23T10:00:00Z", "1"))); err == nil {
		t.Error("invalid job ID accepted")
	}
	if _, err := Speed.Normalize(Job{ID: id}, []byte(speed("2026-09-23T10:00:00Z", "1"))); err == nil {
		t.Error("empty staging key accepted")
	}
}
