(function () {
    "use strict";

    function isKcuStudentYes() {
        var checked = document.querySelector('input[name="is_kcu_student"]:checked');
        return checked && checked.value === "yes";
    }

    function syncDepartmentField() {
        var departmentInput = document.getElementById("id_department");
        if (!departmentInput) {
            return;
        }

        var enableDepartment = isKcuStudentYes();
        departmentInput.disabled = !enableDepartment;
        departmentInput.closest(".mb-3")?.classList.toggle("opacity-50", !enableDepartment);
        if (!enableDepartment) {
            departmentInput.value = "";
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll('input[name="is_kcu_student"]').forEach(function (el) {
            el.addEventListener("change", syncDepartmentField);
        });
        syncDepartmentField();
    });
})();
