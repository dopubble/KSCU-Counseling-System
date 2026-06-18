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

    function initPasswordSection() {
        var form = document.querySelector(".profile-update-form");
        var section = document.getElementById("profilePasswordSection");
        if (!form || !section) {
            return;
        }

        var passwordFields = ["old_password", "new_password1", "new_password2"];

        function clearPasswordFields() {
            passwordFields.forEach(function (name) {
                var input = form.querySelector('[name="' + name + '"]');
                if (input) {
                    input.value = "";
                }
            });
        }

        function isPasswordSectionOpen() {
            return section.classList.contains("show");
        }

        form.addEventListener("submit", function () {
            if (!isPasswordSectionOpen()) {
                clearPasswordFields();
            }
        });

        section.addEventListener("hidden.bs.collapse", clearPasswordFields);
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll('input[name="is_kcu_student"]').forEach(function (el) {
            el.addEventListener("change", syncDepartmentField);
        });
        syncDepartmentField();
        initPasswordSection();
    });
})();
