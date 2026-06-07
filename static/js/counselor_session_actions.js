(function () {
    "use strict";

    console.log("counselor_session_actions.js loaded");

    const AJAX_HEADER = { "X-Requested-With": "XMLHttpRequest" };
    const CONFIRM_APPOINTMENT_MSG = "정말 이 예약을 확정하시겠습니까?";
    const CONFIRM_REJECT_MSG = "정말 이 예약을 반려하시겠습니까?";
    const CONFIRM_CANCEL_APPROVE_MSG = "취소 요청을 승인하면 예약이 취소됩니다. 계속하시겠습니까?";
    const CONFIRM_CANCEL_REJECT_MSG = "취소 요청을 반려하시겠습니까? 예약은 그대로 유지됩니다.";
    const CONFIRM_SCHEDULE_CHANGE_APPROVE_MSG =
        "일정 변경 요청을 승인하면 예약 일시가 변경됩니다. 계속하시겠습니까?";
    const CONFIRM_SCHEDULE_CHANGE_REJECT_MSG =
        "일정 변경 요청을 반려하시겠습니까? 기존 일정은 그대로 유지됩니다.";

    const modal = document.getElementById("appointmentRejectModal");
    const rejectForm = document.getElementById("appointmentRejectForm");
    const rejectTarget = document.getElementById("appointmentRejectTarget");
    const rejectReason = document.getElementById("appointmentRejectReason");
    const cancelRejectModal = document.getElementById("cancelRejectModal");
    const cancelRejectForm = document.getElementById("cancelRejectForm");
    const cancelRejectTarget = document.getElementById("cancelRejectTarget");
    const cancelRejectReason = document.getElementById("cancelRejectReason");
    const scheduleChangeRejectModal = document.getElementById("scheduleChangeRejectModal");
    const scheduleChangeRejectForm = document.getElementById("scheduleChangeRejectForm");
    const scheduleChangeRejectTarget = document.getElementById("scheduleChangeRejectTarget");
    const scheduleChangeRejectReason = document.getElementById("scheduleChangeRejectReason");
    let activeSessionNumber = "";

    function getCsrfToken() {
        const fromInput = document.querySelector(
            "input[name=csrfmiddlewaretoken]"
        );
        if (fromInput && fromInput.value) {
            return fromInput.value;
        }
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    function buildFetchHeaders() {
        const headers = Object.assign({}, AJAX_HEADER);
        const csrf = getCsrfToken();
        if (csrf) {
            headers["X-CSRFToken"] = csrf;
        }
        return headers;
    }

    function buildConfirmFormData(btn, form) {
        const formData = form ? new FormData(form) : new FormData();
        const csrf = getCsrfToken();
        if (csrf && !formData.has("csrfmiddlewaretoken")) {
            formData.append("csrfmiddlewaretoken", csrf);
        }
        const sessionId =
            btn.getAttribute("data-session-id") ||
            btn.getAttribute("data-session-number") ||
            form?.getAttribute("data-session-number") ||
            "";
        const appointmentId =
            btn.getAttribute("data-appointment-id") ||
            form?.getAttribute("data-appointment-id") ||
            "";
        if (sessionId && !formData.has("session_id")) {
            formData.append("session_id", sessionId);
        }
        if (appointmentId && !formData.has("appointment_id")) {
            formData.append("appointment_id", appointmentId);
        }
        return formData;
    }

    function showToast(message, type) {
        if (!message) return;
        const alertClass = type === "error" ? "alert-danger" : "alert-success";
        const toast = document.createElement("div");
        toast.className =
            "alert " + alertClass + " alert-dismissible fade show shadow-sm counselor-session-toast";
        toast.setAttribute("role", "alert");
        toast.innerHTML =
            message +
            '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="닫기"></button>';
        const container = document.querySelector(".counselor-page .site-container");
        if (container) {
            container.prepend(toast);
        } else {
            document.body.prepend(toast);
        }
        window.setTimeout(function () {
            if (toast.parentNode) {
                toast.classList.remove("show");
                window.setTimeout(function () {
                    toast.remove();
                }, 200);
            }
        }, 5000);
    }

    function replaceSessionCard(html, sessionNumber) {
        const card = document.getElementById("session-" + sessionNumber);
        if (!card) return;
        card.outerHTML = html;
        updatePendingAlert(sessionNumber);
    }

    function removeScheduleChangeSidebarItem(sessionNumber) {
        if (!sessionNumber) {
            return;
        }
        const sidebar = document.querySelector(".counselor-schedule-change-pending-card");
        if (!sidebar) {
            return;
        }
        const item = sidebar.querySelector(
            '[data-schedule-change-session="' + sessionNumber + '"]'
        );
        if (item) {
            item.remove();
        }
        if (!sidebar.querySelector(".counselor-pending-request-card")) {
            sidebar.remove();
        }
    }

    function updatePendingAlert(processedSessionNumber) {
        const pendingCards = document.querySelectorAll('[data-session-status="requested"]');
        const alert = document.getElementById("counselorPendingAlert");
        if (alert && pendingCards.length === 0) {
            alert.remove();
        }
        const cancelPendingCards = document.querySelectorAll('[data-session-status="cancel_pending"]');
        const cancelAlert = document.getElementById("counselorCancelPendingAlert");
        if (cancelAlert && cancelPendingCards.length === 0) {
            cancelAlert.remove();
        }
        const cancelSidebar = document.querySelector(".counselor-cancel-pending-card");
        if (cancelSidebar && cancelPendingCards.length === 0) {
            cancelSidebar.remove();
        }
        const changeRequestedCards = document.querySelectorAll(
            '[data-session-status="change_requested"]'
        );
        const scheduleChangeAlert = document.getElementById("counselorScheduleChangeAlert");
        if (scheduleChangeAlert && changeRequestedCards.length === 0) {
            scheduleChangeAlert.remove();
        }
        if (processedSessionNumber) {
            removeScheduleChangeSidebarItem(processedSessionNumber);
        }
        if (
            changeRequestedCards.length === 0 &&
            !document.querySelector(".counselor-schedule-change-pending-card")
        ) {
            const scheduleSidebar = document.querySelector(
                ".counselor-schedule-change-pending-card"
            );
            if (scheduleSidebar) {
                scheduleSidebar.remove();
            }
        }
    }

    async function postSessionAction(url, formData) {
        console.log("fetch POST", url, Object.fromEntries(formData.entries()));
        const response = await fetch(url, {
            method: "POST",
            body: formData,
            headers: buildFetchHeaders(),
            credentials: "same-origin",
        });

        const message = response.headers.get("X-Session-Message");

        if (response.ok) {
            const html = await response.text();
            return { ok: true, html: html, message: message };
        }

        let errorMessage = "요청 처리 중 오류가 발생했습니다.";
        try {
            const data = await response.json();
            if (data && data.error) {
                errorMessage = data.error;
            }
        } catch (e) {
            /* ignore */
        }
        return { ok: false, message: errorMessage };
    }

    async function submitConfirmRequest(btn, sessionNumber) {
        const form = btn.closest(".counselor-session-confirm-form");
        const actionUrl =
            btn.getAttribute("data-confirm-url") ||
            (form && form.getAttribute("action")) ||
            "";

        if (!actionUrl || actionUrl === "#") {
            showToast("연결된 예약 정보가 없습니다.", "error");
            return;
        }

        const formData = buildConfirmFormData(btn, form);
        const actionBtn = btn;
        actionBtn.disabled = true;

        console.log("fetch 시작", actionUrl);
        const result = await postSessionAction(actionUrl, formData);

        actionBtn.disabled = false;
        if (result.ok) {
            replaceSessionCard(result.html, sessionNumber);
            showToast(result.message, "success");
        } else {
            showToast(result.message, "error");
        }
    }

    function openCancelRejectModal(trigger) {
        if (!cancelRejectModal || !cancelRejectForm || !trigger) {
            return;
        }
        cancelRejectForm.action = trigger.getAttribute("data-reject-url") || "";
        activeSessionNumber =
            trigger.getAttribute("data-session-number") ||
            trigger.closest("[data-session-number]")?.getAttribute("data-session-number") ||
            "";
        if (cancelRejectTarget) {
            cancelRejectTarget.textContent = trigger.getAttribute("data-session-label") || "";
        }
        if (cancelRejectReason) {
            cancelRejectReason.value = "";
        }
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(cancelRejectModal).show();
        }
    }

    function handleCancelApproveClick(event, btn) {
        event.preventDefault();
        event.stopPropagation();

        const sessionNumber = btn.getAttribute("data-session-number");
        const actionUrl = btn.getAttribute("data-approve-url");
        if (!actionUrl) {
            showToast("연결된 예약 정보가 없습니다.", "error");
            return;
        }
        if (!window.confirm(CONFIRM_CANCEL_APPROVE_MSG)) {
            return;
        }

        const form = btn.closest(".counselor-session-cancel-approve-form");
        const formData = buildConfirmFormData(btn, form);
        btn.disabled = true;
        postSessionAction(actionUrl, formData).then(function (result) {
            btn.disabled = false;
            if (result.ok) {
                replaceSessionCard(result.html, sessionNumber);
                showToast(result.message, "success");
            } else {
                showToast(result.message, "error");
            }
        });
    }

    function handleCancelRejectClick(event, btn) {
        event.preventDefault();
        event.stopPropagation();
        if (!btn.getAttribute("data-reject-url")) {
            showToast("연결된 예약 정보가 없습니다.", "error");
            return;
        }
        if (!window.confirm(CONFIRM_CANCEL_REJECT_MSG)) {
            return;
        }
        openCancelRejectModal(btn);
    }

    function openScheduleChangeRejectModal(trigger) {
        if (!scheduleChangeRejectModal || !scheduleChangeRejectForm || !trigger) {
            return;
        }
        scheduleChangeRejectForm.action = trigger.getAttribute("data-reject-url") || "";
        activeSessionNumber =
            trigger.getAttribute("data-session-number") ||
            trigger.closest("[data-session-number]")?.getAttribute("data-session-number") ||
            "";
        if (scheduleChangeRejectTarget) {
            scheduleChangeRejectTarget.textContent =
                trigger.getAttribute("data-session-label") || "";
        }
        if (scheduleChangeRejectReason) {
            scheduleChangeRejectReason.value = "";
        }
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(scheduleChangeRejectModal).show();
        }
    }

    function handleScheduleChangeApproveClick(event, btn) {
        event.preventDefault();
        event.stopPropagation();

        const sessionNumber = btn.getAttribute("data-session-number");
        const actionUrl = btn.getAttribute("data-approve-url");
        if (!actionUrl) {
            showToast("연결된 요청 정보가 없습니다.", "error");
            return;
        }
        if (!window.confirm(CONFIRM_SCHEDULE_CHANGE_APPROVE_MSG)) {
            return;
        }

        const form = btn.closest(".counselor-session-schedule-change-approve-form");
        const formData = buildConfirmFormData(btn, form);
        btn.disabled = true;
        postSessionAction(actionUrl, formData).then(function (result) {
            btn.disabled = false;
            if (result.ok) {
                replaceSessionCard(result.html, sessionNumber);
                showToast(result.message, "success");
            } else {
                showToast(result.message, "error");
            }
        });
    }

    function handleScheduleChangeRejectClick(event, btn) {
        event.preventDefault();
        event.stopPropagation();
        if (!btn.getAttribute("data-reject-url")) {
            showToast("연결된 요청 정보가 없습니다.", "error");
            return;
        }
        if (!window.confirm(CONFIRM_SCHEDULE_CHANGE_REJECT_MSG)) {
            return;
        }
        openScheduleChangeRejectModal(btn);
    }

    function openRejectModal(trigger) {
        if (!modal || !rejectForm || !trigger) {
            console.log("openRejectModal: modal 또는 trigger 없음");
            return;
        }
        rejectForm.action = trigger.getAttribute("data-reject-url") || "";
        activeSessionNumber =
            trigger.getAttribute("data-session-id") ||
            trigger.getAttribute("data-session-number") ||
            trigger.closest("[data-session-number]")?.getAttribute("data-session-number") ||
            "";
        if (rejectTarget) {
            rejectTarget.textContent = trigger.getAttribute("data-session-label") || "";
        }
        if (rejectReason) rejectReason.value = "";

        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(modal).show();
        }
    }

    function handleConfirmClick(event, btn) {
        console.log("버튼 클릭됨");
        event.preventDefault();
        event.stopPropagation();

        const sessionId =
            btn.getAttribute("data-session-id") ||
            btn.getAttribute("data-session-number") ||
            btn.closest("[data-session-id]")?.getAttribute("data-session-id") ||
            btn.closest("[data-session-number]")?.getAttribute("data-session-number");
        const appointmentId = btn.getAttribute("data-appointment-id");
        const actionUrl = btn.getAttribute("data-confirm-url");

        console.log("전송할 세션 ID:", sessionId);
        console.log("전송할 예약 ID:", appointmentId);
        console.log("confirm 호출 전", { sessionId: sessionId, appointmentId: appointmentId, actionUrl: actionUrl });

        if (!actionUrl || !appointmentId) {
            showToast("연결된 예약 정보가 없습니다.", "error");
            return;
        }

        if (!window.confirm(CONFIRM_APPOINTMENT_MSG)) {
            console.log("confirm 취소됨");
            return;
        }

        console.log("confirm 확인됨");
        submitConfirmRequest(btn, sessionId);
    }

    function handleRejectClick(event, btn) {
        console.log("버튼 클릭됨", "reject");
        event.preventDefault();
        event.stopPropagation();

        if (!btn.getAttribute("data-reject-url")) {
            showToast("연결된 예약 정보가 없습니다.", "error");
            return;
        }

        console.log("confirm 호출 전", "reject");
        if (!window.confirm(CONFIRM_REJECT_MSG)) {
            return;
        }

        openRejectModal(btn);
    }

    document.addEventListener(
        "click",
        function (event) {
            const confirmBtn = event.target.closest(".confirm-btn");
            if (confirmBtn) {
                handleConfirmClick(event, confirmBtn);
                return;
            }

            const rejectBtn = event.target.closest(".appointment-reject-btn");
            if (rejectBtn) {
                handleRejectClick(event, rejectBtn);
                return;
            }

            const cancelApproveBtn = event.target.closest(".counselor-cancel-approve-btn");
            if (cancelApproveBtn) {
                handleCancelApproveClick(event, cancelApproveBtn);
                return;
            }

            const cancelRejectBtn = event.target.closest(".counselor-cancel-reject-btn");
            if (cancelRejectBtn) {
                handleCancelRejectClick(event, cancelRejectBtn);
                return;
            }

            const scheduleChangeApproveBtn = event.target.closest(
                ".counselor-schedule-change-approve-btn"
            );
            if (scheduleChangeApproveBtn) {
                handleScheduleChangeApproveClick(event, scheduleChangeApproveBtn);
                return;
            }

            const scheduleChangeRejectBtn = event.target.closest(
                ".counselor-schedule-change-reject-btn"
            );
            if (scheduleChangeRejectBtn) {
                handleScheduleChangeRejectClick(event, scheduleChangeRejectBtn);
            }
        },
        true
    );

    window.confirmAppointment = function (event, sessionNumber) {
        const btn =
            (event && event.target && event.target.closest(".confirm-btn")) ||
            document.querySelector("#session-" + sessionNumber + " .confirm-btn");
        if (btn) {
            handleConfirmClick(event || { preventDefault: function () {}, stopPropagation: function () {} }, btn);
        }
    };

    window.rejectAppointment = function (event) {
        const btn =
            event && event.target && event.target.closest(".appointment-reject-btn");
        if (btn) {
            handleRejectClick(event || { preventDefault: function () {}, stopPropagation: function () {} }, btn);
        }
    };

    if (rejectForm) {
        rejectForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            if (!rejectForm.action) return;
            const formData = new FormData(rejectForm);
            const csrf = getCsrfToken();
            if (csrf && !formData.has("csrfmiddlewaretoken")) {
                formData.append("csrfmiddlewaretoken", csrf);
            }
            const submitBtn = rejectForm.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            const result = await postSessionAction(rejectForm.action, formData);
            if (submitBtn) submitBtn.disabled = false;
            if (result.ok) {
                if (modal && window.bootstrap && window.bootstrap.Modal) {
                    const bsModal = window.bootstrap.Modal.getInstance(modal);
                    if (bsModal) bsModal.hide();
                }
                if (activeSessionNumber) {
                    replaceSessionCard(result.html, activeSessionNumber);
                }
                showToast(result.message, "success");
            } else {
                showToast(result.message, "error");
            }
        });
    }

    if (cancelRejectForm) {
        cancelRejectForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            if (!cancelRejectForm.action) {
                return;
            }
            const formData = new FormData(cancelRejectForm);
            const csrf = getCsrfToken();
            if (csrf && !formData.has("csrfmiddlewaretoken")) {
                formData.append("csrfmiddlewaretoken", csrf);
            }
            const submitBtn = cancelRejectForm.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
            }
            const result = await postSessionAction(cancelRejectForm.action, formData);
            if (submitBtn) {
                submitBtn.disabled = false;
            }
            if (result.ok) {
                if (cancelRejectModal && window.bootstrap && window.bootstrap.Modal) {
                    const bsModal = window.bootstrap.Modal.getInstance(cancelRejectModal);
                    if (bsModal) {
                        bsModal.hide();
                    }
                }
                if (activeSessionNumber) {
                    replaceSessionCard(result.html, activeSessionNumber);
                }
                showToast(result.message, "success");
            } else {
                showToast(result.message, "error");
            }
        });
    }

    if (scheduleChangeRejectForm) {
        scheduleChangeRejectForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            if (!scheduleChangeRejectForm.action) {
                return;
            }
            const formData = new FormData(scheduleChangeRejectForm);
            const csrf = getCsrfToken();
            if (csrf && !formData.has("csrfmiddlewaretoken")) {
                formData.append("csrfmiddlewaretoken", csrf);
            }
            const submitBtn = scheduleChangeRejectForm.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
            }
            const result = await postSessionAction(scheduleChangeRejectForm.action, formData);
            if (submitBtn) {
                submitBtn.disabled = false;
            }
            if (result.ok) {
                if (
                    scheduleChangeRejectModal &&
                    window.bootstrap &&
                    window.bootstrap.Modal
                ) {
                    const bsModal = window.bootstrap.Modal.getInstance(
                        scheduleChangeRejectModal
                    );
                    if (bsModal) {
                        bsModal.hide();
                    }
                }
                if (activeSessionNumber) {
                    replaceSessionCard(result.html, activeSessionNumber);
                }
                showToast(result.message, "success");
            } else {
                showToast(result.message, "error");
            }
        });
    }

    const toastStyle = document.createElement("style");
    toastStyle.textContent =
        ".counselor-session-toast { position: sticky; top: 1rem; z-index: 1050; }";
    document.head.appendChild(toastStyle);
})();
