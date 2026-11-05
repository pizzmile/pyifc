(function () {
  "use strict";

  angular.module("sensorHub", [])
    .directive("fileReader", function () {
      return {
        restrict: "A",
        scope: { fileReader: "=", fileReaderError: "=" },
        link: function (scope, element) {
          element.on("change", function (event) {
            var file = event.target.files[0];
            if (!file) return;
            scope.$apply(function () { scope.fileReaderError = ""; });
            if (file.size > 4096) {
              scope.$apply(function () {
                scope.fileReader = "";
                scope.fileReaderError = "This file is larger than 4 KiB.";
              });
              element.val(null);
              return;
            }
            var reader = new FileReader();
            reader.onload = function (loadEvent) {
              scope.$apply(function () {
                scope.fileReader = loadEvent.target.result;
              });
            };
            reader.onerror = function () {
              scope.$apply(function () {
                scope.fileReaderError = "The selected file could not be read.";
              });
            };
            reader.readAsText(file);
          });
        },
      };
    })
    .controller("AppController", function ($http, $interval, $scope, $timeout) {
      var app = this;
      var pollPromise = null;
      var chart = null;
      var pollStarted = 0;

      app.page = "trains";
      app.trains = [];
      app.readings = [];
      app.detailReadings = [];
      app.selectedTrain = null;
      app.loading = false;
      app.detailLoading = false;
      app.submitting = false;
      app.error = "";
      app.submitState = {};
      app.readingText = "";
      app.fileError = "";
      app.lastUpdated = null;

      app.navigate = function (page, event) {
        if (event) event.preventDefault();
        app.page = page;
        app.error = "";
        if (page === "trains") app.loadOverview();
      };

      app.byteLength = function (value) {
        return new TextEncoder().encode(value || "").length;
      };

      app.loadExample = function (kind) {
        var reading = {
          kind: kind,
          fleet_id: "demo-fleet",
          asset_id: "train-01",
          timestamp: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
          value: kind === "speed" ? 132.6 : 26680,
        };
        app.readingText = JSON.stringify(reading, null, 2);
        app.submitState = {};
      };

      app.loadOverview = function () {
        app.loading = true;
        app.error = "";
        $http.get("/readings", { params: { limit: 100 } }).then(function (response) {
          app.readings = response.data.readings || [];
          app.trains = groupTrains(app.readings);
          app.lastUpdated = new Date();
        }).catch(function () {
          app.error = "Could not load readings. Check that the API is available and refresh.";
        }).finally(function () {
          app.loading = false;
        });
      };

      app.openTrain = function (train) {
        app.selectedTrain = train;
        app.detailReadings = [];
        app.detailLoading = true;
        app.error = "";
        $http.get("/readings", {
          params: { limit: 100, fleet_id: train.fleet_id, asset_id: train.asset_id },
        }).then(function (response) {
          app.detailReadings = response.data.readings || [];
          $timeout(function () { drawChart(app.detailReadings); }, 0);
        }).catch(function () {
          app.error = "Could not load this train’s history. Refresh to try again.";
        }).finally(function () {
          app.detailLoading = false;
        });
      };

      app.closeTrain = function () {
        app.selectedTrain = null;
        destroyChart();
        app.loadOverview();
      };

      app.submitReading = function () {
        app.submitState = {};
        var reading;
        try {
          reading = JSON.parse(app.readingText);
        } catch (error) {
          app.submitState = { kind: "notice-error", message: "This is not valid JSON. Check the payload syntax." };
          return;
        }
        if (!reading || Array.isArray(reading) || typeof reading !== "object") {
          app.submitState = { kind: "notice-error", message: "Submit one JSON object, not an array or a scalar." };
          return;
        }
        if (app.byteLength(app.readingText) > 4096) {
          app.submitState = { kind: "notice-error", message: "The payload exceeds the 4 KiB limit." };
          return;
        }

        app.submitting = true;
        $http.post("/readings", reading, {
          headers: { "Content-Type": "application/json" },
        }).then(function (response) {
          app.submitState = {
            kind: "notice-success",
            message: "Accepted. Waiting for the worker to process this reading.",
            id: response.data.id,
          };
          beginProcessingPoll(response.data.id, reading.fleet_id, reading.asset_id);
        }).catch(function (response) {
          var detail = response.data && response.data.error;
          app.submitState = {
            kind: "notice-error",
            message: detail && detail.message ? detail.message : "The API rejected this reading. Review the required fields and try again.",
          };
        }).finally(function () {
          app.submitting = false;
        });
      };

      function groupTrains(readings) {
        var groups = Object.create(null);
        readings.forEach(function (reading) {
          var key = reading.fleet_id + "\u0000" + reading.asset_id;
          if (!groups[key]) {
            groups[key] = {
              key: key,
              fleet_id: reading.fleet_id,
              asset_id: reading.asset_id,
              latest: {},
              latestReading: reading,
            };
          }
          if (!groups[key].latest[reading.kind]) groups[key].latest[reading.kind] = reading;
          if (reading.timestamp > groups[key].latestReading.timestamp) {
            groups[key].latestReading = reading;
          }
        });
        return Object.keys(groups).map(function (key) { return groups[key]; })
          .sort(function (left, right) {
            return left.fleet_id.localeCompare(right.fleet_id) || left.asset_id.localeCompare(right.asset_id);
          });
      }

      function beginProcessingPoll(readingId, fleetId, assetId) {
        if (pollPromise) $interval.cancel(pollPromise);
        pollStarted = Date.now();
        pollPromise = $interval(function () {
          if (Date.now() - pollStarted > 45000) {
            $interval.cancel(pollPromise);
            pollPromise = null;
            return;
          }
          $http.get("/readings", { params: { limit: 100 } }).then(function (response) {
            var found = (response.data.readings || []).some(function (reading) {
              return reading.id === readingId;
            });
            if (found) {
              $interval.cancel(pollPromise);
              pollPromise = null;
              app.submitState.message = "Processed and visible in the fleet view.";
              if (app.page === "trains") {
                app.loadOverview();
                if (app.selectedTrain && app.selectedTrain.fleet_id === fleetId && app.selectedTrain.asset_id === assetId) {
                  app.openTrain(app.selectedTrain);
                }
              }
            }
          });
        }, 2000);
      }

      function drawChart(readings) {
        destroyChart();
        var canvas = document.getElementById("train-history-chart");
        if (!canvas || !window.Chart) return;
        var chronological = readings.slice().reverse();
        var labels = chronological.map(function (reading) {
          var date = new Date(reading.timestamp);
          return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        });
        chart = new window.Chart(canvas, {
          type: "line",
          data: {
            labels: labels,
            datasets: [
              {
                label: "Speed (km/h)",
                data: chronological.map(function (reading) { return reading.kind === "speed" ? reading.value : null; }),
                borderColor: "#087e68",
                backgroundColor: "rgba(8, 126, 104, .10)",
                pointBackgroundColor: "#087e68",
                pointRadius: 3,
                borderWidth: 2,
                spanGaps: true,
                yAxisID: "ySpeed",
                tension: 0.25,
              },
              {
                label: "Voltage (V)",
                data: chronological.map(function (reading) { return reading.kind === "line_voltage" ? reading.value : null; }),
                borderColor: "#d57b35",
                backgroundColor: "rgba(213, 123, 53, .08)",
                pointBackgroundColor: "#d57b35",
                pointRadius: 3,
                borderWidth: 2,
                spanGaps: true,
                yAxisID: "yVoltage",
                tension: 0.25,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "nearest", intersect: false },
            plugins: { legend: { display: false } },
            scales: {
              x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
              ySpeed: { type: "linear", position: "left", title: { display: true, text: "km/h" }, grid: { color: "#e8ede7" } },
              yVoltage: { type: "linear", position: "right", title: { display: true, text: "V" }, grid: { drawOnChartArea: false } },
            },
          },
        });
      }

      function destroyChart() {
        if (chart) {
          chart.destroy();
          chart = null;
        }
      }

      $scope.$on("$destroy", function () {
        if (pollPromise) $interval.cancel(pollPromise);
        destroyChart();
      });

      app.loadOverview();
    });
}());