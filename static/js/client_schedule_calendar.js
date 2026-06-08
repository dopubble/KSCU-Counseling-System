/**
 * 내담자 예약 달력 — 상담사 가용·차단 일정 시각화 (Flatpickr)
 */
(function (global) {
    "use strict";

    function toPythonWeekday(date) {
        const jsDay = date.getDay();
        return jsDay === 0 ? 6 : jsDay - 1;
    }

    function formatISODate(date) {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, "0");
        const d = String(date.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + d;
    }

    /** Django DateTimeField 입력 규격 — YYYY-MM-DD HH:MM (24시간, 0 패딩) */
    function formatDatetimeForServer(value, selectedDate) {
        let date = null;
        if (selectedDate instanceof Date && !Number.isNaN(selectedDate.getTime())) {
            date = selectedDate;
        } else if (value) {
            const trimmed = String(value).trim().replace("T", " ");
            const match = trimmed.match(
                /^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/
            );
            if (match) {
                date = new Date(
                    Number(match[1]),
                    Number(match[2]) - 1,
                    Number(match[3]),
                    match[4] !== undefined ? Number(match[4]) : 0,
                    match[5] !== undefined ? Number(match[5]) : 0,
                    0,
                    0
                );
            }
        }
        if (!date || Number.isNaN(date.getTime())) {
            return "";
        }
        const y = date.getFullYear();
        const mo = String(date.getMonth() + 1).padStart(2, "0");
        const d = String(date.getDate()).padStart(2, "0");
        const h = String(date.getHours()).padStart(2, "0");
        const mi = String(date.getMinutes()).padStart(2, "0");
        return y + "-" + mo + "-" + d + " " + h + ":" + mi;
    }

    function resolveInputDatetimeForServer(input) {
        if (!input) {
            return "";
        }
        if (input._flatpickr && input._flatpickr.selectedDates.length) {
            return formatDatetimeForServer(
                "",
                input._flatpickr.selectedDates[0]
            );
        }
        return formatDatetimeForServer(input.value, null);
    }

    /**
     * 날짜별 표시 상태.
     * specific(일회성) > blocked_dates > recurring 순. 특정일 차단이 정기 가능 요일을 덮어씀.
     */
    function resolveDateAvailability(date, rules, blockedDates) {
        if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
            return null;
        }

        const dateStr = formatISODate(date);

        if (blockedDates && blockedDates.indexOf(dateStr) !== -1) {
            return "blocked";
        }

        const specific = (rules || []).filter(function (rule) {
            return rule.specific_date === dateStr;
        });
        if (specific.length) {
            if (specific.some(function (rule) { return !rule.is_available; })) {
                return "blocked";
            }
            if (specific.some(function (rule) { return rule.is_available; })) {
                return "available";
            }
        }

        const dow = toPythonWeekday(date);
        const recurring = (rules || []).filter(function (rule) {
            return rule.is_recurring && rule.day_of_week === dow;
        });
        if (recurring.length) {
            const hasAvailable = recurring.some(function (rule) { return rule.is_available; });
            const hasBlocked = recurring.some(function (rule) { return !rule.is_available; });
            if (hasBlocked && !hasAvailable) {
                return "blocked";
            }
            if (hasAvailable) {
                return "available";
            }
            if (hasBlocked) {
                return "blocked";
            }
        }

        return null;
    }

    function isBlockedDateValue(value, rules, blockedDates, selectedDate) {
        if (!value && !selectedDate) {
            return false;
        }
        return !isAvailableDateValue(value, rules, blockedDates, selectedDate);
    }

    function isAvailableDateValue(value, rules, blockedDates, selectedDate) {
        let date = null;
        if (selectedDate instanceof Date && !Number.isNaN(selectedDate.getTime())) {
            date = selectedDate;
        } else if (value) {
            const datePart = value.slice(0, 10);
            const parts = datePart.split("-");
            if (parts.length === 3) {
                date = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
            }
        }
        if (!date) {
            return false;
        }
        return resolveDateAvailability(date, rules, blockedDates) === "available";
    }

    function parseLocalDateTime(value, selectedDate) {
        if (selectedDate instanceof Date && !Number.isNaN(selectedDate.getTime())) {
            return selectedDate;
        }
        if (!value) {
            return null;
        }
        const trimmed = String(value).trim().replace("T", " ");
        const match = trimmed.match(
            /^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/
        );
        if (!match) {
            return null;
        }
        return new Date(
            Number(match[1]),
            Number(match[2]) - 1,
            Number(match[3]),
            match[4] !== undefined ? Number(match[4]) : 0,
            match[5] !== undefined ? Number(match[5]) : 0,
            0,
            0
        );
    }

    function getRulesForLocalDate(date, rules) {
        const dateStr = formatISODate(date);
        const specific = (rules || []).filter(function (rule) {
            return !rule.is_recurring && rule.specific_date === dateStr;
        });
        const dow = toPythonWeekday(date);
        const recurring = (rules || []).filter(function (rule) {
            return rule.is_recurring && rule.day_of_week === dow;
        });
        return { dateStr: dateStr, specific: specific, recurring: recurring };
    }

    function combineLocalDateTime(date, timeStr) {
        const parts = timeStr.split(":");
        return new Date(
            date.getFullYear(),
            date.getMonth(),
            date.getDate(),
            Number(parts[0]),
            Number(parts[1]),
            0,
            0
        );
    }

    function getAvailabilityWindowLabels(date, rules, blockedDates) {
        if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
            return [];
        }
        const dateStr = formatISODate(date);
        if (blockedDates && blockedDates.indexOf(dateStr) !== -1) {
            return [];
        }
        const parsed = getRulesForLocalDate(date, rules);
        let allows = parsed.specific.filter(function (rule) {
            return rule.is_available;
        });
        if (!allows.length) {
            allows = parsed.recurring.filter(function (rule) {
                return rule.is_available;
            });
        }
        if (!allows.length) {
            if (!parsed.specific.length && !parsed.recurring.length) {
                return [];
            }
            return [];
        }
        return allows.map(function (rule) {
            return rule.start_time + "~" + rule.end_time;
        });
    }

    function isCounselorSlotStartAvailable(value, rules, blockedDates, selectedDate) {
        const slotStart = parseLocalDateTime(value, selectedDate);
        if (!slotStart) {
            return false;
        }
        const dateStr = formatISODate(slotStart);
        if (blockedDates && blockedDates.indexOf(dateStr) !== -1) {
            return false;
        }

        const parsed = getRulesForLocalDate(slotStart, rules);
        const slotMs = slotStart.getTime();

        function withinWindow(startStr, endStr) {
            const windowStart = combineLocalDateTime(slotStart, startStr);
            const windowEnd = combineLocalDateTime(slotStart, endStr);
            return windowStart.getTime() <= slotMs && slotMs <= windowEnd.getTime();
        }

        for (let i = 0; i < parsed.specific.length; i++) {
            const rule = parsed.specific[i];
            if (!rule.is_available) {
                if (withinWindow(rule.start_time, rule.end_time)) {
                    return false;
                }
            }
        }

        const specificAllows = parsed.specific.filter(function (rule) {
            return rule.is_available;
        });
        if (specificAllows.length) {
            for (let i = 0; i < specificAllows.length; i++) {
                if (withinWindow(specificAllows[i].start_time, specificAllows[i].end_time)) {
                    return true;
                }
            }
            return false;
        }

        if (!parsed.recurring.length) {
            return true;
        }

        for (let i = 0; i < parsed.recurring.length; i++) {
            const rule = parsed.recurring[i];
            if (!rule.is_available && withinWindow(rule.start_time, rule.end_time)) {
                return false;
            }
        }

        const recurringAllows = parsed.recurring.filter(function (rule) {
            return rule.is_available;
        });
        if (!recurringAllows.length) {
            return false;
        }

        for (let i = 0; i < recurringAllows.length; i++) {
            if (withinWindow(recurringAllows[i].start_time, recurringAllows[i].end_time)) {
                return true;
            }
        }
        return false;
    }

    function findFirstAvailableSlotStart(date, rules, blockedDates) {
        const labels = getAvailabilityWindowLabels(date, rules, blockedDates);
        for (let i = 0; i < labels.length; i++) {
            const startStr = labels[i].split("~")[0];
            const candidate = combineLocalDateTime(date, startStr);
            if (isCounselorSlotStartAvailable("", rules, blockedDates, candidate)) {
                return candidate;
            }
        }
        return null;
    }

    function formatAvailabilityHint(date, rules, blockedDates) {
        const labels = getAvailabilityWindowLabels(date, rules, blockedDates);
        if (!labels.length) {
            return "";
        }
        return "가능 시간: " + labels.join(", ");
    }

    function rejectInvalidSlotSelection(selectedDates, dateStr, instance, rules, blockedDates, options) {
        if (options && typeof options.onInvalidSlot === "function") {
            options.onInvalidSlot(dateStr, selectedDates[0], rules, blockedDates);
        }
        instance.clear();
        instance._scheduleUserPickedDay = false;
        decorateFlatpickrDays(instance, rules, blockedDates);
        bindTimeInteractionGuard(instance, rules, blockedDates);
    }

    function rejectUnavailableSelection(selectedDates, dateStr, instance, rules, blockedDates, options) {
        if (options && typeof options.onBlockedSelect === "function") {
            options.onBlockedSelect(dateStr);
        }
        instance.clear();
        instance._scheduleUserPickedDay = false;
        decorateFlatpickrDays(instance, rules, blockedDates);
        bindTimeInteractionGuard(instance, rules, blockedDates);
    }

    function revertTimeOnlyChange(instance, rules, blockedDates) {
        if (!instance) {
            return;
        }
        instance.selectedDates.length = 0;
        if (instance.input) {
            instance.input.value = "";
        }
        if (instance.calendarContainer) {
            instance.calendarContainer.querySelectorAll(".flatpickr-day.selected").forEach(function (dayElem) {
                dayElem.classList.remove("selected", "startRange", "endRange", "inRange");
            });
        }
        decorateFlatpickrDays(instance, rules, blockedDates);
    }

    function bindTimeInteractionGuard(instance, rules, blockedDates) {
        if (!instance || !instance.calendarContainer) {
            return;
        }
        const timeContainer = instance.calendarContainer.querySelector(".flatpickr-time");
        if (!timeContainer || timeContainer.dataset.scheduleTimeGuardBound === "1") {
            return;
        }
        timeContainer.dataset.scheduleTimeGuardBound = "1";
        timeContainer.addEventListener(
            "mousedown",
            function (event) {
                if (instance._scheduleUserPickedDay) {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();
                decorateFlatpickrDays(instance, rules, blockedDates);
            },
            true
        );
        timeContainer.addEventListener(
            "keydown",
            function (event) {
                if (instance._scheduleUserPickedDay) {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();
                decorateFlatpickrDays(instance, rules, blockedDates);
            },
            true
        );
    }

    function confirmScheduleDayIfValidOnOpen(instance, rules, blockedDates) {
        if (!instance) {
            return;
        }
        const value = instance.input && instance.input.value;
        instance._scheduleUserPickedDay = !!(
            value && isAvailableDateValue(value, rules, blockedDates)
        );
    }

    function bindDayClickTracker(dayElem, instance) {
        if (!dayElem || !instance || dayElem.dataset.scheduleDayClickBound === "1") {
            return;
        }
        dayElem.dataset.scheduleDayClickBound = "1";
        dayElem.addEventListener("click", function () {
            instance._scheduleUserPickedDay = true;
            window.setTimeout(function () {
                decorateFlatpickrDays(
                    instance,
                    instance._scheduleRules,
                    instance._scheduleBlockedDates
                );
            }, 0);
        });
    }

    function applyDayAvailabilityClass(dayElem, date, rules, blockedDates) {
        if (!dayElem || !date) {
            return;
        }
        dayElem.classList.remove("counselor-day-available", "counselor-day-blocked");
        const status = resolveDateAvailability(date, rules, blockedDates);
        if (status === "available") {
            dayElem.classList.add("counselor-day-available");
            const hint = formatAvailabilityHint(date, rules, blockedDates);
            dayElem.setAttribute(
                "title",
                hint ? "상담 가능 · " + hint : "상담 가능 요일·일정"
            );
        } else if (status === "blocked") {
            dayElem.classList.add("counselor-day-blocked");
            dayElem.setAttribute("title", "상담 불가(차단) 요일·일정");
        } else {
            dayElem.removeAttribute("title");
        }
    }

    function decorateFlatpickrDays(instance, rules, blockedDates) {
        if (!instance || !instance.calendarContainer) {
            return;
        }
        instance.calendarContainer.querySelectorAll(".flatpickr-day").forEach(function (dayElem) {
            if (dayElem.classList.contains("prevMonthDay") || dayElem.classList.contains("nextMonthDay")) {
                return;
            }
            const dateObj = dayElem.dateObj;
            if (dateObj) {
                applyDayAvailabilityClass(dayElem, dateObj, rules, blockedDates);
            }
        });
    }

    function getSchedulePickerInputs() {
        return document.querySelectorAll(
            ".client-schedule-datetime-input, [name='preferred_datetime'], [name='scheduled_at']"
        );
    }

    function getFlatpickrInstanceFromTarget(target) {
        if (!target) {
            return null;
        }
        if (target._flatpickr) {
            return target._flatpickr;
        }
        const calendarEl = target.closest && target.closest(".flatpickr-calendar");
        if (!calendarEl) {
            return null;
        }
        let matched = null;
        getSchedulePickerInputs().forEach(function (input) {
            if (input._flatpickr && input._flatpickr.calendarContainer === calendarEl) {
                matched = input._flatpickr;
            }
        });
        return matched;
    }

    function isFlatpickrCalendarTarget(target) {
        return !!(target && target.closest && target.closest(".flatpickr-calendar"));
    }

    function forceCloseAllSchedulePickers() {
        getSchedulePickerInputs().forEach(function (input) {
            if (input._flatpickr) {
                input._flatpickr.close();
            }
        });
        document.querySelectorAll(".flatpickr-calendar.open").forEach(function (calendarEl) {
            calendarEl.classList.remove("open");
        });
    }

    /** 인풋에 Enter 직접 바인딩 — Flatpickr 훅보다 먼저 달력 강제 닫기 */
    function bindEnterForceClose(input) {
        if (!input || input.dataset.scheduleEnterCloseBound === "1") {
            return;
        }
        input.dataset.scheduleEnterCloseBound = "1";
        input.addEventListener(
            "keydown",
            function (event) {
                if (event.key !== "Enter") {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();
                if (this._flatpickr) {
                    this._flatpickr.close();
                }
            },
            true
        );
    }

    /** document capture — 열린 예약 달력에서 Enter 시 모든 스케줄 picker 강제 닫기 */
    function ensureDocumentEnterCaptureClose() {
        if (global._scheduleCalendarEnterCaptureBound) {
            return;
        }
        global._scheduleCalendarEnterCaptureBound = true;
        document.addEventListener(
            "keydown",
            function (event) {
                if (event.key !== "Enter") {
                    return;
                }
                const calendarContainer = document.querySelector(".flatpickr-calendar.open");
                if (!calendarContainer) {
                    return;
                }
                let isScheduleCalendar = false;
                getSchedulePickerInputs().forEach(function (input) {
                    if (input._flatpickr && input._flatpickr.calendarContainer === calendarContainer) {
                        isScheduleCalendar = true;
                    }
                });
                if (!isScheduleCalendar) {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();
                forceCloseAllSchedulePickers();
            },
            true
        );
    }

    /** 모달 폼 Enter 가드 — submit만 차단 (달력 닫기는 bindEnterForceClose가 처리) */
    function handleEnterForSchedulePicker(event) {
        if (!event || (event.key !== "Enter" && event.keyCode !== 13)) {
            return false;
        }
        if (isFlatpickrCalendarTarget(event.target)) {
            return true;
        }
        const instance = getFlatpickrInstanceFromTarget(event.target);
        if (instance && instance.isOpen) {
            event.preventDefault();
            return true;
        }
        return false;
    }

    function bindDayDoubleClickSelect(dayElem, dObj, instance, rules, blockedDates, options) {
        if (!dayElem || !dObj || !instance || dayElem.dataset.scheduleDblclickBound === "1") {
            return;
        }
        dayElem.dataset.scheduleDblclickBound = "1";
        dayElem.addEventListener("dblclick", function (event) {
            event.preventDefault();
            event.stopPropagation();
            if (dayElem.classList.contains("flatpickr-disabled")) {
                return;
            }
            instance.setDate(dObj, true);
            const currentValue = instance.input ? instance.input.value : "";
            if (!isAvailableDateValue(currentValue, rules, blockedDates, dObj)) {
                rejectUnavailableSelection([dObj], currentValue, instance, rules, blockedDates, options);
                return;
            }
            instance.close();
        });
    }

    function closePicker(input) {
        if (!input || !input._flatpickr) {
            return;
        }
        input._flatpickr.close();
    }

    function closePickersIn(root) {
        if (!root) {
            return;
        }
        root.querySelectorAll(
            ".client-schedule-datetime-input, [name='preferred_datetime'], [name='scheduled_at']"
        ).forEach(closePicker);
    }

    /** 모달 닫힘 등으로 남는 Flatpickr 레이어 정리 */
    function closeAllOpenCalendars() {
        document.querySelectorAll(
            ".client-schedule-datetime-input, [name='preferred_datetime'], [name='scheduled_at']"
        ).forEach(closePicker);
        document.querySelectorAll(".flatpickr-calendar.open").forEach(function (calendarEl) {
            calendarEl.classList.remove("open");
        });
    }

    function initPicker(input, rules, blockedDates, options) {
        if (!input || !global.flatpickr || input.dataset.schedulePickerBound === "1") {
            if (input) {
                bindEnterForceClose(input);
                ensureDocumentEnterCaptureClose();
            }
            return input && input._flatpickr ? input._flatpickr : null;
        }
        input.dataset.schedulePickerBound = "1";

        const config = Object.assign(
            {
                enableTime: true,
                time_24hr: true,
                dateFormat: "Y-m-d H:i",
                altInput: false,
                allowInput: true,
                minuteIncrement: 1,
                locale: global.flatpickr.l10ns.ko || undefined,
                onReady: function (_selected, _str, instance) {
                    instance._scheduleRules = rules;
                    instance._scheduleBlockedDates = blockedDates;
                    decorateFlatpickrDays(instance, rules, blockedDates);
                    bindTimeInteractionGuard(instance, rules, blockedDates);
                },
                onMonthChange: function (_selected, _str, instance) {
                    decorateFlatpickrDays(instance, rules, blockedDates);
                },
                onYearChange: function (_selected, _str, instance) {
                    decorateFlatpickrDays(instance, rules, blockedDates);
                },
                onOpen: function (_selected, _str, instance) {
                    decorateFlatpickrDays(instance, rules, blockedDates);
                    confirmScheduleDayIfValidOnOpen(instance, rules, blockedDates);
                    bindTimeInteractionGuard(instance, rules, blockedDates);
                },
                onDayCreate: function (dObj, _dStr, instance, dayElem) {
                    applyDayAvailabilityClass(dayElem, dObj, rules, blockedDates);
                    bindDayClickTracker(dayElem, instance);
                    bindDayDoubleClickSelect(dayElem, dObj, instance, rules, blockedDates, options);
                },
                onChange: function (selectedDates, dateStr, instance) {
                    if (!selectedDates.length) {
                        return;
                    }
                    if (!instance._scheduleUserPickedDay) {
                        revertTimeOnlyChange(instance, rules, blockedDates);
                        return;
                    }
                    if (!isAvailableDateValue(dateStr, rules, blockedDates, selectedDates[0])) {
                        rejectUnavailableSelection(selectedDates, dateStr, instance, rules, blockedDates, options);
                        return;
                    }
                    if (instance.input) {
                        instance.input.value = formatDatetimeForServer(
                            dateStr,
                            selectedDates[0]
                        );
                    }
                    decorateFlatpickrDays(instance, rules, blockedDates);
                },
                onValueUpdate: function (_selected, _str, instance) {
                    decorateFlatpickrDays(instance, rules, blockedDates);
                },
            },
            options && options.flatpickr ? options.flatpickr : {}
        );

        const instance = global.flatpickr(input, config);
        bindEnterForceClose(input);
        ensureDocumentEnterCaptureClose();
        return instance;
    }

    function bindBlockedDateGuard(input, rules, blockedDates, onBlocked) {
        if (!input || input.dataset.blockedDateGuardBound === "1") {
            return;
        }
        input.dataset.blockedDateGuardBound = "1";
        input.addEventListener("change", function () {
            if (!input.value) {
                return;
            }
            if (!isAvailableDateValue(input.value, rules, blockedDates)) {
                if (typeof onBlocked === "function") {
                    onBlocked(input.value);
                }
                input.value = "";
                if (input._flatpickr) {
                    input._flatpickr.clear();
                }
            }
        });
    }

    global.ClientScheduleCalendar = {
        resolveDateAvailability: resolveDateAvailability,
        isBlockedDateValue: isBlockedDateValue,
        isAvailableDateValue: isAvailableDateValue,
        isCounselorSlotStartAvailable: isCounselorSlotStartAvailable,
        formatAvailabilityHint: formatAvailabilityHint,
        formatDatetimeForServer: formatDatetimeForServer,
        resolveInputDatetimeForServer: resolveInputDatetimeForServer,
        initPicker: initPicker,
        bindBlockedDateGuard: bindBlockedDateGuard,
        decorateFlatpickrDays: decorateFlatpickrDays,
        closePicker: closePicker,
        closePickersIn: closePickersIn,
        closeAllOpenCalendars: closeAllOpenCalendars,
        isFlatpickrCalendarTarget: isFlatpickrCalendarTarget,
        getFlatpickrInstanceFromTarget: getFlatpickrInstanceFromTarget,
        handleEnterForSchedulePicker: handleEnterForSchedulePicker,
    };
})(window);
