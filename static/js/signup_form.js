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

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll('input[name="role"], input[name="is_kcu_student"]').forEach(function (el) {
            el.addEventListener("change", syncSignupClientFields);
        });
        syncSignupClientFields();
    });
})();
