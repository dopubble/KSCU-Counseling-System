/**
 * 예약 캘린더 (FullCalendar v6 + 슬롯 패널)
 * #bookingCalendarRoot[data-config] JSON 설정
 */
(function () {
    "use strict";

    const root = document.getElementById("bookingCalendarRoot");
    if (!root || typeof FullCalendar === "undefined") {
        return;
    }

    const configEl = document.getElementById("booking-calendar-config");
    let config = {};
    try {
        config = JSON.parse((configEl && configEl.textContent) || "{}");
    } catch (_err) {
        return;
    }

    const calendarEl = document.getElementById("bookingCalendar");
    const loadingEl = document.getElementById("bookingCalendarLoading");
    const slotListEl = document.getElementById("bookingSlotList");
    const slotPanelTitleEl = document.getElementById("bookingSlotPanelTitle");
    const slotPanelNoteEl = document.getElementById("bookingSlotPanelNote");
    const confirmPanelEl = document.getElementById("bookingConfirmPanel");
    const selectedTimeLabelEl = document.getElementById("bookingSelectedTimeLabel");
    const scheduledInput = document.getElementById("bookingScheduledAtInput");
    const durationInput = document.getElementById("bookingDurationInput");
    const submitBtn = document.getElementById("bookingSubmitBtn");
    const blockedDatesEl = document.getElementById("counselor-blocked-dates");
    const rulesEl = document.getElementById("counselor-availability-rules");
    const availableDatesUrl = root.dataset.availableDatesUrl || "";
    const calendarTimeZone = root.dataset.timezone || "Asia/Seoul";

    const counselorBlockedDates = blockedDatesEl
        ? JSON.parse(blockedDatesEl.textContent || "[]")
        : [];
    const counselorAvailabilityRules = rulesEl
        ? JSON.parse(rulesEl.textContent || "[]")
        : [];

    const SLOT_STATE_LABELS = {
        available: "예약 가능",
        blocked: "상담 불가",
        taken: "예약됨",
        zoom_full: "비대면 만석",
        room_full: "대면 상담실 만석",
    };

    let selectedDate = null;
    let selectedSlotStart = null;
    let monthAvailableDates = new Set();
    let loadedMonthKey = "";
    let availabilityReady = false;
    let availabilityLoading = false;
    let loadingSlots = false;
    let calendar = null;
    const isCounselorCalendar = config.role === "counselor";

    function setAvailabilityLoading(loading) {
        availabilityLoading = loading;
        if (root) {
            root.classList.toggle("booking-calendar--loading", loading);
        }
        if (loadingEl) {
            loadingEl.classList.toggle("d-none", !loading);
            loadingEl.setAttribute("aria-hidden", loading ? "false" : "true");
        }
        if (calendar) {
            calendar.render();
        }
    }

    function finishAvailabilityLoad(monthKey, dates) {
        monthAvailableDates = new Set(dates || []);
        loadedMonthKey = monthKey;
        availabilityReady = true;
        setAvailabilityLoading(false);
    }

    function formatEventTime(date) {
        if (!date) return "";
        return FullCalendar.formatDate(date, {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
            timeZone: calendarTimeZone,
        });
    }

    function buildCounselorEventContent(arg) {
        const props = arg.event.extendedProps || {};
        const clientName = (props.client_name || arg.event.title || "").trim();
        const timeText = formatEventTime(arg.event.start);
        const wrap = document.createElement("div");
        wrap.className = "booking-cal-event";

        if (timeText) {
            const timeEl = document.createElement("span");
            timeEl.className = "booking-cal-event-time";
            timeEl.textContent = timeText;
            wrap.appendChild(timeEl);
        }
        if (clientName) {
            const nameEl = document.createElement("span");
            nameEl.className = "booking-cal-event-name";
            nameEl.textContent = clientName;
            wrap.appendChild(nameEl);
        }
        return { domNodes: [wrap] };
    }

    function mountCounselorEvent(info) {
        const props = info.event.extendedProps || {};
        const timeText = formatEventTime(info.event.start);
        const clientName = (props.client_name || info.event.title || "").trim();
        const parts = [timeText, clientName].filter(Boolean);
        if (parts.length) {
            info.el.setAttribute("title", parts.join(" · "));
        }
    }

    function formatDateKey(date) {
        const y = date.getFullYear();
        const m = String(date.getMonth() + 1).padStart(2, "0");
        const d = String(date.getDate()).padStart(2, "0");
        return `${y}-${m}-${d}`;
    }

    function formatDisplayDate(date) {
        return `${date.getFullYear()}년 ${date.getMonth() + 1}월 ${date.getDate()}일`;
    }

    function toFormDatetime(isoString) {
        if (!isoString) return "";
        const dt = new Date(isoString);
        const y = dt.getFullYear();
        const m = String(dt.getMonth() + 1).padStart(2, "0");
        const d = String(dt.getDate()).padStart(2, "0");
        const h = String(dt.getHours()).padStart(2, "0");
        const min = String(dt.getMinutes()).padStart(2, "0");
        return `${y}-${m}-${d} ${h}:${min}`;
    }

    function buildSlotsUrl(dateKey) {
        const params = new URLSearchParams({
            case_id: config.caseId,
            date: dateKey,
        });
        if (config.durationMinutes) {
            params.set("duration_minutes", String(config.durationMinutes));
        }
        if (config.excludeAppointmentId) {
            params.set("exclude_appointment_id", config.excludeAppointmentId);
        }
        return `${config.slotsUrl}?${params.toString()}`;
    }

    function buildAvailableDatesUrl(monthKey) {
        const params = new URLSearchParams({
            case_id: config.caseId,
            month: monthKey,
        });
        if (config.excludeAppointmentId) {
            params.set("exclude_appointment_id", config.excludeAppointmentId);
        }
        return `${availableDatesUrl}?${params.toString()}`;
    }

    function hideConfirmPanel() {
        if (!confirmPanelEl) return;
        confirmPanelEl.classList.add("d-none");
        selectedSlotStart = null;
        if (scheduledInput) scheduledInput.value = "";
        if (submitBtn) submitBtn.disabled = true;
        slotListEl
            ?.querySelectorAll(".booking-slot-btn--selected")
            .forEach((btn) => btn.classList.remove("booking-slot-btn--selected"));
    }

    function showConfirmPanel(slot) {
        if (!confirmPanelEl || !selectedTimeLabelEl || !scheduledInput) return;
        selectedSlotStart = slot.start;
        selectedTimeLabelEl.textContent = `${formatDisplayDate(selectedDate)} ${slot.label}`;
        scheduledInput.value = toFormDatetime(slot.start);
        confirmPanelEl.classList.remove("d-none");
        if (submitBtn) submitBtn.disabled = false;
        if (durationInput && config.durationMinutes) {
            durationInput.value = String(config.durationMinutes);
        }
    }

    function formatCounselorSlotMeta(slot) {
        const stateText = SLOT_STATE_LABELS[slot.state] || slot.state;
        if (
            !isCounselorCalendar ||
            slot.room_remaining === undefined ||
            slot.zoom_remaining === undefined
        ) {
            return stateText;
        }
        return `${stateText} · 대면 ${slot.room_remaining} / 비대면 ${slot.zoom_remaining}`;
    }

    function renderSlots(slots) {
        if (!slotListEl) return;
        slotListEl.innerHTML = "";
        hideConfirmPanel();

        if (!slots.length) {
            slotListEl.innerHTML =
                '<div class="booking-slot-empty">선택한 날짜에 예약 가능한 시간이 없습니다.</div>';
            return;
        }

        slots.forEach((slot) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "booking-slot-btn";
            const disabled = slot.state !== "available";
            btn.disabled = disabled;
            if (disabled) {
                btn.classList.add("booking-slot-btn--disabled");
            }

            const timeSpan = document.createElement("span");
            timeSpan.className = "booking-slot-time";
            timeSpan.textContent = slot.label;

            const stateSpan = document.createElement("span");
            stateSpan.className = "booking-slot-state";
            stateSpan.textContent = formatCounselorSlotMeta(slot);

            btn.appendChild(timeSpan);
            btn.appendChild(stateSpan);

            if (!disabled) {
                btn.addEventListener("click", function () {
                    slotListEl
                        .querySelectorAll(".booking-slot-btn--selected")
                        .forEach((el) => el.classList.remove("booking-slot-btn--selected"));
                    btn.classList.add("booking-slot-btn--selected");
                    showConfirmPanel(slot);
                });
            }

            slotListEl.appendChild(btn);
        });
    }

    async function loadSlots(date) {
        if (!slotListEl || loadingSlots) return;
        loadingSlots = true;
        selectedDate = date;
        const dateKey = formatDateKey(date);
        if (slotPanelTitleEl) {
            slotPanelTitleEl.textContent = `${formatDisplayDate(date)} 시간 선택`;
        }
        if (slotPanelNoteEl && isCounselorCalendar) {
            slotPanelNoteEl.classList.remove("d-none");
        }
        slotListEl.innerHTML =
            '<div class="booking-slot-empty">시간대를 불러오는 중…</div>';

        try {
            const response = await fetch(buildSlotsUrl(dateKey), {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) {
                throw new Error("slots fetch failed");
            }
            const payload = await response.json();
            renderSlots(payload.slots || []);
        } catch (_err) {
            slotListEl.innerHTML =
                '<div class="booking-slot-empty">시간대를 불러오지 못했습니다. 다시 시도해 주세요.</div>';
        } finally {
            loadingSlots = false;
        }
    }

    function currentMonthKey() {
        if (!calendar) return "";
        const activeDate = calendar.getDate();
        return `${activeDate.getFullYear()}-${String(activeDate.getMonth() + 1).padStart(2, "0")}`;
    }

    async function refreshMonthAvailability(_dateInfo) {
        if (!availableDatesUrl || !calendar) return;
        const monthKey = currentMonthKey();
        if (monthKey === loadedMonthKey) {
            return;
        }
        setAvailabilityLoading(true);
        try {
            const response = await fetch(buildAvailableDatesUrl(monthKey), {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) {
                finishAvailabilityLoad(monthKey, []);
                return;
            }
            const payload = await response.json();
            finishAvailabilityLoad(monthKey, payload.available_dates || []);
        } catch (_err) {
            finishAvailabilityLoad(monthKey, []);
        }
    }

    calendar = new FullCalendar.Calendar(calendarEl, {
        locale: "ko",
        timeZone: calendarTimeZone,
        initialView: "dayGridMonth",
        height: "auto",
        expandRows: true,
        fixedWeekCount: false,
        dayHeaders: false,
        dayCellContent: function (arg) {
            return { html: String(arg.date.getDate()) };
        },
        headerToolbar: {
            left: "prev,next today",
            center: "title",
            right: "",
        },
        dayCellClassNames: function (arg) {
            const key = formatDateKey(arg.date);
            if (counselorBlockedDates.includes(key)) {
                return ["booking-day-blocked"];
            }
            if (!availabilityReady || availabilityLoading) {
                return ["booking-day-loading"];
            }
            if (monthAvailableDates.has(key)) {
                return ["booking-day-available"];
            }
            return ["booking-day-blocked"];
        },
        dateClick: function (info) {
            if (!availabilityReady || availabilityLoading) {
                return;
            }
            const key = formatDateKey(info.date);
            if (counselorBlockedDates.includes(key)) {
                return;
            }
            if (!monthAvailableDates.has(key)) {
                return;
            }
            calendar.getEvents().forEach((event) => {
                if (event.display === "background") {
                    event.remove();
                }
            });
            calendar.addEvent({
                start: info.date,
                allDay: true,
                display: "background",
                classNames: ["booking-day-selected"],
            });
            loadSlots(info.date);
        },
        datesSet: function (dateInfo) {
            refreshMonthAvailability(dateInfo);
        },
        ...(isCounselorCalendar
            ? {
                  dayMaxEvents: false,
                  dayMaxEventRows: false,
                  eventDisplay: "block",
                  eventOrder: "start",
                  eventClassNames: ["booking-cal-counselor-event"],
                  eventContent: buildCounselorEventContent,
                  eventDidMount: mountCounselorEvent,
              }
            : {}),
        events: function (fetchInfo, successCallback, failureCallback) {
            if (config.role !== "counselor" || !config.eventsUrl) {
                successCallback([]);
                return;
            }
            const params = new URLSearchParams({
                start: fetchInfo.startStr,
                end: fetchInfo.endStr,
            });
            fetch(`${config.eventsUrl}?${params.toString()}`, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            })
                .then((response) => {
                    if (!response.ok) throw new Error("events fetch failed");
                    return response.json();
                })
                .then((events) => successCallback(events))
                .catch(() => failureCallback());
        },
        eventClick: function () {
            /* 상담사 일정은 조회만 — 예약은 빈 슬롯에서 */
        },
    });

    calendar.render();

    const bookingForm = document.getElementById("bookingCalendarForm");
    if (bookingForm) {
        bookingForm.addEventListener("submit", function (event) {
            if (!scheduledInput || !scheduledInput.value) {
                event.preventDefault();
                alert("예약할 시간을 선택해 주세요.");
                return;
            }
            if (config.role === "counselor") {
                const when = scheduledInput.value.replace("T", " ");
                const message = when
                    ? `선택하신 ${when}로 ${config.sessionNumber}회기 예약을 확정하시겠습니까?`
                    : "예약을 확정하시겠습니까?";
                if (!window.confirm(message)) {
                    event.preventDefault();
                }
            }
        });
    }
})();
