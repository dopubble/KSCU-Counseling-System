/**
 * 비대면 Zoom 동시 예약 용량 — 달력 UI용
 */
(function (global) {
    "use strict";

    var FULL_MESSAGE =
        "해당 시간대 비대면 상담 예약이 만석입니다. 다른 시간을 선택해 주세요.";

    function formatISODate(date) {
        var y = date.getFullYear();
        var m = String(date.getMonth() + 1).padStart(2, "0");
        var d = String(date.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + d;
    }

    function countOverlaps(slotStart, durationMinutes, intervals) {
        if (!slotStart || !intervals || !intervals.length) {
            return 0;
        }
        var startMs = slotStart.getTime();
        var endMs = startMs + durationMinutes * 60 * 1000;
        var count = 0;
        intervals.forEach(function (interval) {
            var iStart = new Date(interval.start).getTime();
            var iEnd = new Date(interval.end).getTime();
            if (startMs < iEnd && iStart < endMs) {
                count += 1;
            }
        });
        return count;
    }

    function isSlotAvailable(slotStart, durationMinutes, intervals, capacity) {
        if (!capacity || capacity < 1) {
            return true;
        }
        return countOverlaps(slotStart, durationMinutes, intervals) < capacity;
    }

    function fetchIntervals(url, rangeStart, rangeEnd, excludeAppointmentId) {
        var params = new URLSearchParams({
            start: formatISODate(rangeStart) + "T00:00:00",
            end: formatISODate(rangeEnd) + "T23:59:59",
        });
        if (excludeAppointmentId) {
            params.set("exclude_appointment_id", excludeAppointmentId);
        }
        return fetch(url + "?" + params.toString(), {
            credentials: "same-origin",
            headers: { Accept: "application/json" },
        }).then(function (response) {
            if (!response.ok) {
                throw new Error("intervals fetch failed");
            }
            return response.json();
        });
    }

    function refreshIntervalsForInstance(instance, zoomState) {
        if (!instance || !zoomState || !zoomState.enabled || !zoomState.url) {
            return Promise.resolve();
        }
        var month = instance.currentMonth;
        var year = instance.currentYear;
        var rangeStart = new Date(year, month, 1);
        var rangeEnd = new Date(year, month + 2, 0);
        return fetchIntervals(
            zoomState.url,
            rangeStart,
            rangeEnd,
            zoomState.excludeAppointmentId
        )
            .then(function (data) {
                zoomState.intervals = data.intervals || [];
                if (data.capacity) {
                    zoomState.capacity = data.capacity;
                }
                if (data.default_duration_minutes && !zoomState.durationMinutes) {
                    zoomState.durationMinutes = data.default_duration_minutes;
                }
                instance.redraw();
            })
            .catch(function () {
                zoomState.intervals = zoomState.intervals || [];
            });
    }

    function buildZoomState(config, getDurationMinutes) {
        if (!config || !config.remote) {
            return null;
        }
        return {
            enabled: true,
            url: config.zoomIntervalsUrl,
            capacity: config.zoomCapacity || 2,
            durationMinutes: config.durationMinutes || 60,
            excludeAppointmentId: config.excludeAppointmentId || "",
            intervals: [],
            getDurationMinutes: getDurationMinutes,
            onFull: function () {
                alert(FULL_MESSAGE);
            },
        };
    }

    function isZoomSlotBlocked(date, zoomState) {
        if (!zoomState || !zoomState.enabled) {
            return false;
        }
        var duration = zoomState.getDurationMinutes
            ? zoomState.getDurationMinutes()
            : zoomState.durationMinutes;
        return !isSlotAvailable(
            date,
            duration,
            zoomState.intervals,
            zoomState.capacity
        );
    }

    global.RemoteZoomSchedule = {
        FULL_MESSAGE: FULL_MESSAGE,
        countOverlaps: countOverlaps,
        isSlotAvailable: isSlotAvailable,
        fetchIntervals: fetchIntervals,
        refreshIntervalsForInstance: refreshIntervalsForInstance,
        buildZoomState: buildZoomState,
        isZoomSlotBlocked: isZoomSlotBlocked,
    };
})(window);
