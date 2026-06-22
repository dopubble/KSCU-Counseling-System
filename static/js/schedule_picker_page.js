/**
 * 상담 일정 Flatpickr — 내담자·상담사 공통 초기화
 */
(function () {
    "use strict";

    function readJsonScript(id, fallback) {
        var el = document.getElementById(id);
        if (!el) {
            return fallback;
        }
        try {
            return JSON.parse(el.textContent);
        } catch (err) {
            return fallback;
        }
    }

    function getDurationMinutes(config) {
        var durationInput = document.querySelector('[name="duration_minutes"]');
        if (durationInput && durationInput.value) {
            var parsed = parseInt(durationInput.value, 10);
            if (!Number.isNaN(parsed) && parsed > 0) {
                return parsed;
            }
        }
        return (config && config.durationMinutes) || 60;
    }

    function buildZoomOptions(config) {
        if (!window.RemoteZoomSchedule || !config || !config.remote) {
            return null;
        }
        return {
            enabled: true,
            url: config.zoomIntervalsUrl,
            capacity: config.zoomCapacity || 2,
            durationMinutes: config.durationMinutes || 60,
            excludeAppointmentId: config.excludeAppointmentId || "",
            getDurationMinutes: function () {
                return getDurationMinutes(config);
            },
            onFull: function () {
                alert(window.RemoteZoomSchedule.FULL_MESSAGE);
            },
        };
    }

    function initInput(input, rules, blockedDates, config) {
        if (!input || !window.ClientScheduleCalendar || !window.flatpickr) {
            return;
        }
        var zoom = buildZoomOptions(config);
        ClientScheduleCalendar.initPicker(input, rules, blockedDates, {
            zoom: zoom,
            onInvalidSlot: function () {
                /* counselor slot — handled in calendar */
            },
            onBlockedSelect: function () {
                alert("상담가능 시간이 아닙니다. 파란색 날짜를 선택해주세요.");
            },
            onZoomFull: function () {
                if (window.RemoteZoomSchedule) {
                    alert(window.RemoteZoomSchedule.FULL_MESSAGE);
                }
            },
        });
        ClientScheduleCalendar.bindBlockedDateGuard(
            input,
            rules,
            blockedDates,
            function () {
                alert("상담가능 시간이 아닙니다. 파란색 날짜를 선택해주세요.");
            }
        );
    }

    function initAllPickers() {
        var config = readJsonScript("schedule-picker-config", {});
        var rules = readJsonScript("counselor-availability-rules", []);
        var blockedDates = readJsonScript("counselor-blocked-dates", []);
        document
            .querySelectorAll(
                ".client-schedule-datetime-input, .schedule-datetime-picker, [name='preferred_datetime'], [name='scheduled_at']"
            )
            .forEach(function (input) {
                if (input.type === "datetime-local") {
                    return;
                }
                initInput(input, rules, blockedDates, config);
            });

        var durationInput = document.querySelector('[name="duration_minutes"]');
        if (durationInput) {
            durationInput.addEventListener("change", function () {
                document
                    .querySelectorAll(".client-schedule-datetime-input, .schedule-datetime-picker")
                    .forEach(function (input) {
                        if (input._flatpickr) {
                            input._flatpickr.redraw();
                        }
                    });
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAllPickers);
    } else {
        initAllPickers();
    }
})();
