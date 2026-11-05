// Package reading holds what all services share: the reading types, the JSON
// schemas (copied from sensorhub/src/backend/schema) and the validation and
// normalization rules of the sensor hub. It declares no Encore
// resources and no APIs, so it is a plain library package, not a service.
package reading

import (
	"bytes"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

//go:embed schema/*.json
var schemaFS embed.FS

// MaxBodyBytes is the largest accepted upload (SPEC: 4 KiB).
const MaxBodyBytes = 4096

var (
	// ErrMalformed: the body is not a JSON object (HTTP 400).
	ErrMalformed = errors.New("malformed JSON")
	// ErrInvalid: valid JSON that violates a schema, or an unknown kind (HTTP 422).
	ErrInvalid = errors.New("reading does not match its schema")
)

// Kind describes one reading kind and how its parser normalizes it.
type Kind struct {
	Name           string
	schema         string
	Unit           string
	ZeroStatus     string
	PositiveStatus string
}

var (
	Speed       = Kind{"speed", "speed-reading", "km/h", "stopped", "moving"}
	LineVoltage = Kind{"line_voltage", "line-voltage-reading", "V", "no_voltage_observed", "voltage_observed"}
)

// Job is the queue message: a reference to the staged object, not the reading.
type Job struct {
	ID         string `json:"id"`
	StagingKey string `json:"staging_key"`
}

// Processed is the normalized record stored in the results bucket.
type Processed struct {
	ID        string  `json:"id"`
	Kind      string  `json:"kind"`
	FleetID   string  `json:"fleet_id"`
	AssetID   string  `json:"asset_id"`
	Timestamp string  `json:"timestamp"`
	Value     float64 `json:"value"`
	Unit      string  `json:"unit"`
	Status    string  `json:"status"`
}

var schemas = sync.OnceValue(func() map[string]*jsonschema.Schema {
	c := jsonschema.NewCompiler()
	c.AssertFormat()
	out := map[string]*jsonschema.Schema{}
	for _, name := range []string{"speed-reading", "line-voltage-reading", "reading-job", "processed-reading"} {
		f, err := schemaFS.Open("schema/" + name + ".schema.json")
		if err != nil {
			panic(err)
		}
		doc, err := jsonschema.UnmarshalJSON(f)
		f.Close()
		if err != nil {
			panic(err)
		}
		url := "mem:///" + name + ".json"
		if err := c.AddResource(url, doc); err != nil {
			panic(err)
		}
		out[name] = c.MustCompile(url)
	}
	return out
})

// parse decodes one JSON object. json.Number keeps numbers exact; Go's decoder
// already rejects NaN and Infinity tokens.
func parse(body []byte) (any, error) {
	if !utf8.Valid(body) {
		return nil, ErrMalformed
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(body))
	if err != nil {
		return nil, ErrMalformed
	}
	if _, ok := inst.(map[string]any); !ok {
		return nil, ErrMalformed
	}
	return inst, nil
}

func validate(schema string, inst any) error {
	if err := schemas()[schema].Validate(inst); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalid, err)
	}
	return nil
}

// checkReading adds the checks the schema cannot express (SPEC): a finite
// number, and a real UTC calendar date.
func checkReading(inst any) error {
	m := inst.(map[string]any)
	if n, ok := m["value"].(json.Number); ok {
		f, err := n.Float64()
		if err != nil || math.IsInf(f, 0) || math.IsNaN(f) {
			return fmt.Errorf("%w: value is not finite", ErrInvalid)
		}
	}
	ts, _ := m["timestamp"].(string)
	if !strings.HasSuffix(ts, "Z") {
		return fmt.Errorf("%w: timestamp is not UTC", ErrInvalid)
	}
	if _, err := time.Parse(time.RFC3339, ts); err != nil {
		return fmt.Errorf("%w: timestamp is not a real date", ErrInvalid)
	}
	return nil
}

// KindByName returns the supported kind with that name.
func KindByName(name string) (Kind, bool) {
	for _, k := range []Kind{Speed, LineVoltage} {
		if k.Name == name {
			return k, true
		}
	}
	return Kind{}, false
}

// ValidateUpload is the ingestion check: pick the schema from "kind" and
// validate the whole body against it. It returns the kind for routing.
func ValidateUpload(body []byte) (Kind, error) {
	inst, err := parse(body)
	if err != nil {
		return Kind{}, err
	}
	name, _ := inst.(map[string]any)["kind"].(string)
	kind, ok := KindByName(name)
	if !ok {
		return Kind{}, fmt.Errorf("%w: unknown kind", ErrInvalid)
	}
	if err := kind.check(inst); err != nil {
		return Kind{}, err
	}
	return kind, nil
}

// check validates a reading against this kind's own schema only.
func (k Kind) check(inst any) error {
	if err := validate(k.schema, inst); err != nil {
		return err
	}
	return checkReading(inst)
}

// ValidateJob checks a queue message against the reading-job schema.
func ValidateJob(j Job) error {
	b, _ := json.Marshal(j)
	inst, err := parse(b)
	if err != nil {
		return err
	}
	return validate("reading-job", inst)
}

// Normalize is the parser step. It validates the staged object against this
// kind's schema (a reading of the other kind fails here), builds the processed
// record and returns it as JSON, ready to be stored under job.ID.
func (k Kind) Normalize(job Job, staged []byte) ([]byte, error) {
	if err := ValidateJob(job); err != nil {
		return nil, err
	}
	inst, err := parse(staged)
	if err != nil {
		return nil, err
	}
	if err := k.check(inst); err != nil {
		return nil, err
	}
	var in struct {
		FleetID   string  `json:"fleet_id"`
		AssetID   string  `json:"asset_id"`
		Timestamp string  `json:"timestamp"`
		Value     float64 `json:"value"`
	}
	if err := json.Unmarshal(staged, &in); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrInvalid, err)
	}
	status := k.PositiveStatus
	if in.Value == 0 {
		status = k.ZeroStatus
	}
	out := Processed{job.ID, k.Name, in.FleetID, in.AssetID, in.Timestamp, in.Value, k.Unit, status}
	b, err := json.Marshal(out)
	if err != nil {
		return nil, err
	}
	// Self-check against the processed-reading schema.
	chk, err := parse(b)
	if err != nil {
		return nil, err
	}
	if err := validate("processed-reading", chk); err != nil {
		return nil, err
	}
	return b, nil
}
