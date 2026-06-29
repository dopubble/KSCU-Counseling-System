/**
 * 관리자 예약 관리 캘린더 (FullCalendar v6)
 * data-events-url, data-mock-url, data-timezone on #adminAppointmentCalendar
 */
(function () {
    "use strict";

    const MOBILE_MAX_WIDTH = 767.98;

    const calendarEl = document.getElementById("adminAppointmentCalendar");
    if (!calendarEl || typeof FullCalendar === "undefined") {
        return;
    }

    const eventsUrl = calendarEl.dataset.eventsUrl || "";
    const mockUrl = calendarEl.dataset.mockUrl || "";
    const useMock = calendarEl.dataset.useMock === "1";
    const calendarTimeZone = calendarEl.dataset.timezone || "Asia/Seoul";
    const gcalUi = calendarEl.dataset.gcalUi === "1";
    const detailModalEl = document.getElementById("adminAppointmentDetailModal");
    const detailModal = detailModalEl
        ? window.bootstrap.Modal.getOrCreateInstance(detailModalEl)
        : null;

    function isMobileViewport() {
        return window.matchMedia(`(max-width: ${MOBILE_MAX_WIDTH}px)`).matches;
    }

    /** @type {FullCalendar.Calendar | null} */
    let calendar = null;

    /**
     * FullCalendar timeZone 모드에서는 event.start 가 fake-UTC Date 이므로
     * toLocaleString 대신 calendar.formatDate 를 사용해야 격자·모달 시각이 일치한다.
     */
    function formatScheduledRange(event) {
        const start = event.start;
        const end = event.end;
        if (!start || !calendar) {
            return "—";
        }
        const startLabel = calendar.formatDate(start, {
            year: "numeric",
            month: "long",
            day: "numeric",
            weekday: "short",
            hour: "numeric",
            minute: "2-digit",
            meridiem: "short",
            hour12: true,
        });
        if (!end) {
            return startLabel;
        }
        const endLabel = calendar.formatDate(end, {
            hour: "numeric",
            minute: "2-digit",
            meridiem: "short",
            hour12: true,
        });
        return `${startLabel} ~ ${endLabel}`;
    }

    function setDetail(id, label, value, hidden) {
        const row = document.getElementById(id);
        if (!row) return;
        const valueEl = row.querySelector("[data-detail-value]");
        if (valueEl) {
            valueEl.textContent = value || "—";
        }
        row.classList.toggle("d-none", Boolean(hidden));
    }

    function openDetailModal(event) {
        if (!detailModal) return;
        const props = event.extendedProps || {};
        const sessionLabel = props.session_number
            ? `${props.session_number}회차`
            : "—";

        setDetail("detailClient", "내담자", props.client_name || event.title);
        setDetail("detailCounselor", "담당 상담사", props.counselor_name);
        setDetail("detailSession", "상담 회차", sessionLabel);
        setDetail("detailScheduled", "확정 일시", formatScheduledRange(event));
        setDetail("detailMethod", "상담 방식", props.counseling_method_label);
        setDetail("detailStatus", "예약 상태", props.status_label);
        setDetail("detailCase", "사례 번호", props.case_number);

        const hasHost = Boolean(props.zoom_host_id);
        setDetail(
            "detailZoomHost",
            "줌 호스트",
            props.zoom_host_label || props.zoom_host_id,
            !hasHost,
        );

        const zoomBtn = document.getElementById("detailZoomJoinBtn");
        const noZoomMsg = document.getElementById("detailNoZoomMsg");
        const zoomUrl = (props.zoom_url || "").trim();
        if (zoomBtn) {
            if (zoomUrl) {
                zoomBtn.href = zoomUrl;
                zoomBtn.classList.remove("d-none");
            } else {
                zoomBtn.classList.add("d-none");
            }
        }
        if (noZoomMsg) {
            const showNoZoom =
                props.counseling_method === "REMOTE" && !zoomUrl;
            noZoomMsg.classList.toggle("d-none", !showNoZoom);
        }

        detailModal.show();
    }

    function mobileEventContent(arg) {
        const timeText = (arg.timeText || "").trim();
        const title = arg.event.title || "";
        const timeHtml = timeText
            ? `<div class="fc-mobile-event-time">${timeText}</div>`
            : "";
        return {
            html:
                `<div class="fc-mobile-event">` +
                `${timeHtml}` +
                `<div class="fc-mobile-event-title">${title}</div>` +
                `</div>`,
        };
    }

    function headerToolbarForViewport() {
        if (isMobileViewport()) {
            return {
                left: "prev,next",
                center: "title",
                right: "today",
            };
        }
        return {
            left: "prev,next today",
            center: "title",
            right: "dayGridMonth,timeGridWeek,timeGridDay",
        };
    }

    calendar = new FullCalendar.Calendar(calendarEl, {
        locale: "ko",
        timeZone: calendarTimeZone,
        initialView: "dayGridMonth",
        height: "auto",
        expandRows: true,
        nowIndicator: true,
        slotMinTime: "08:00:00",
        slotMaxTime: "24:00:00",
        slotDuration: "00:30:00",
        allDaySlot: false,
        dayMaxEvents: false,
        dayMaxEventRows: false,
        eventOrder: "start",
        eventDisplay: gcalUi ? "block" : "auto",
        displayEventTime: true,
        eventTimeFormat: {
            hour: "numeric",
            minute: "2-digit",
            meridiem: "short",
            hour12: true,
        },
        headerToolbar: headerToolbarForViewport(),
        buttonText: {
            today: "오늘",
            month: "월",
            week: "주",
            day: "일",
        },
        dayCellContent: function (arg) {
            return { html: String(arg.date.getDate()) };
        },
        eventContent: function (arg) {
            if (isMobileViewport() && arg.view.type === "dayGridMonth") {
                return mobileEventContent(arg);
            }
            return true;
        },
        events: function (info, successCallback, failureCallback) {
            const url = useMock && mockUrl ? mockUrl : eventsUrl;
            if (!url) {
                successCallback([]);
                return;
            }
            const params = new URLSearchParams({
                start: info.startStr,
                end: info.endStr,
            });
            fetch(`${url}?${params.toString()}`, {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("일정을 불러오지 못했습니다.");
                    }
                    return response.json();
                })
                .then(function (data) {
                    successCallback(data.events || []);
                })
                .catch(function (err) {
                    console.error(err);
                    failureCallback(err);
                });
        },
        eventClick: function (info) {
            info.jsEvent.preventDefault();
            openDetailModal(info.event);
        },
        eventDidMount: function (info) {
            const props = info.event.extendedProps || {};
            const scheduled = formatScheduledRange(info.event);
            const parts = [scheduled, info.event.title];
            if (props.counselor_name) {
                parts.push(`상담사: ${props.counselor_name}`);
            }
            if (props.zoom_host_label) {
                parts.push(props.zoom_host_label);
            }
            info.el.title = parts.join(" · ");
            info.el.setAttribute("role", "button");
            info.el.setAttribute("tabindex", "0");
            info.el.addEventListener("keydown", function (ev) {
                if (ev.key === "Enter" || ev.key === " ") {
                    ev.preventDefault();
                    openDetailModal(info.event);
                }
            });
        },
    });

    calendar.render();

    let resizeTimer;
    window.addEventListener("resize", function () {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(function () {
            calendar.setOption("headerToolbar", headerToolbarForViewport());
            calendar.render();
        }, 150);
    });
})();
