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
    const slotListEl = document.getElementById("bookingSlotList");
    const slotPanelTitleEl = document.getElementById("bookingSlotPanelTitle");
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
    let loadingSlots = false;

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
            timeSpan.textContent = slot.label;

            const stateSpan = document.createElement("span");
            stateSpan.className = "booking-slot-state";
            stateSpan.textContent = SLOT_STATE_LABELS[slot.state] || slot.state;

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

    async function refreshMonthAvailability(dateInfo) {
        if (!availableDatesUrl) return;
        const anchor = dateInfo.view.currentStart;
        const monthKey = `${anchor.getFullYear()}-${String(anchor.getMonth() + 1).padStart(2, "0")}`;
        try {
            const response = await fetch(buildAvailableDatesUrl(monthKey), {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) return;
            const payload = await response.json();
            monthAvailableDates = new Set(payload.available_dates || []);
            if (calendar) {
                calendar.render();
            }
        } catch (_err) {
            /* ignore */
        }
    }

    const calendar = new FullCalendar.Calendar(calendarEl, {
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
            if (monthAvailableDates.has(key)) {
                return ["booking-day-available"];
            }
            return ["booking-day-blocked"];
        },
        dateClick: function (info) {
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
