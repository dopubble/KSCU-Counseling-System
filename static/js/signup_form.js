(function () {
    "use strict";

    function selectedRole() {
        var checked = document.querySelector('input[name="role"]:checked');
        return checked ? checked.value : "";
    }

    function isKcuStudentYes() {
        var checked = document.querySelector('input[name="is_kcu_student"]:checked');
        return checked && checked.value === "yes";
    }

    function syncSignupClientFields() {
        var clientSection = document.getElementById("signup-client-fields");
        var departmentInput = document.getElementById("id_department");
        if (!clientSection) {
            return;
        }

        var isClient = selectedRole() === "CLIENT";
        clientSection.style.display = isClient ? "" : "none";

        if (!departmentInput) {
            return;
        }

        var enableDepartment = isClient && isKcuStudentYes();
        departmentInput.disabled = !enableDepartment;
        departmentInput.closest(".mb-3")?.classList.toggle("opacity-50", !enableDepartment);
        if (!enableDepartment) {
            departmentInput.value = "";
        }
    }

    function consentItems() {
        return Array.prototype.slice.call(
            document.querySelectorAll(".signup-consent-item")
        );
    }

    function allConsentChecked() {
        var items = consentItems();
        return items.length > 0 && items.every(function (el) {
            return el.checked;
        });
    }

    function syncSubmitButton() {
        var submitBtn = document.getElementById("signup-submit-btn");
        if (!submitBtn) {
            return;
        }
        submitBtn.disabled = !allConsentChecked();
    }

    function syncAgreeAllCheckbox() {
        var agreeAll = document.getElementById("signup-agree-all");
        if (!agreeAll) {
            return;
        }

        var items = consentItems();
        var checkedCount = items.filter(function (el) {
            return el.checked;
        }).length;

        agreeAll.checked = items.length > 0 && checkedCount === items.length;
        agreeAll.indeterminate = checkedCount > 0 && checkedCount < items.length;
    }

    function setAllConsent(checked) {
        consentItems().forEach(function (el) {
            el.checked = checked;
        });
        syncAgreeAllCheckbox();
        syncSubmitButton();
    }

    function initSignupConsent() {
        var agreeAll = document.getElementById("signup-agree-all");
        if (!agreeAll) {
            return;
        }

        agreeAll.addEventListener("change", function () {
            setAllConsent(agreeAll.checked);
        });

        consentItems().forEach(function (el) {
            el.addEventListener("change", function () {
                syncAgreeAllCheckbox();
                syncSubmitButton();
            });
        });

        syncAgreeAllCheckbox();
        syncSubmitButton();
    }

    /**
     * 서비스 이용약관 전문 페이지로 이동할 때 사용할 URL (추후 전용 페이지 연동 시).
     * 현재는 Bootstrap 모달(#serviceTermsModal)로 약관을 표시합니다.
     */
    function openServiceTermsPage() {
        // 예: window.location.href = "/accounts/terms/";
        var modalEl = document.getElementById("serviceTermsModal");
        if (modalEl && window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll('input[name="role"], input[name="is_kcu_student"]').forEach(function (el) {
            el.addEventListener("change", syncSignupClientFields);
        });
        syncSignupClientFields();
        initSignupConsent();

        window.openServiceTermsPage = openServiceTermsPage;
    });
})();
