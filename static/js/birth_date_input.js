(function () {
    "use strict";

    var ERROR_MESSAGE = "올바른 생년월일을 입력해 주세요.";
    var ERROR_CLASS = "birth-date-input-error";

    function digitsOnly(value) {
        return (value || "").replace(/\D/g, "").slice(0, 8);
    }

    function formatBirthDate(digits) {
        if (digits.length <= 4) {
            return digits;
        }
        if (digits.length <= 6) {
            return digits.slice(0, 4) + "-" + digits.slice(4);
        }
        return digits.slice(0, 4) + "-" + digits.slice(4, 6) + "-" + digits.slice(6);
    }

    function countDigitsBefore(value, index) {
        return value.slice(0, index).replace(/\D/g, "").length;
    }

    function cursorAfterDigits(formatted, digitCount) {
        if (digitCount <= 0) {
            return 0;
        }
        var seen = 0;
        for (var i = 0; i < formatted.length; i += 1) {
            if (/\d/.test(formatted[i])) {
                seen += 1;
                if (seen >= digitCount) {
                    return i + 1;
                }
            }
        }
        return formatted.length;
    }

    function isValidBirthDate(value) {
        if (!value || value.length !== 10) {
            return false;
        }

        var match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!match) {
            return false;
        }

        var year = Number(match[1]);
        var month = Number(match[2]);
        var day = Number(match[3]);

        if (month < 1 || month > 12 || day < 1 || day > 31) {
            return false;
        }

        var parsed = new Date(year, month - 1, day);
        if (
            parsed.getFullYear() !== year ||
            parsed.getMonth() !== month - 1 ||
            parsed.getDate() !== day
        ) {
            return false;
        }

        var today = new Date();
        today.setHours(0, 0, 0, 0);
        parsed.setHours(0, 0, 0, 0);
        return parsed <= today;
    }

    function fieldContainer(input) {
        return input.closest(".mb-3") || input.parentElement;
    }

    function getErrorElement(input) {
        var container = fieldContainer(input);
        if (!container) {
            return null;
        }
        return container.querySelector("." + ERROR_CLASS);
    }

    function clearError(input) {
        input.classList.remove("is-invalid");
        var errorEl = getErrorElement(input);
        if (errorEl) {
            errorEl.remove();
        }
    }

    function showError(input) {
        input.classList.add("is-invalid");
        var container = fieldContainer(input);
        if (!container || getErrorElement(input)) {
            return;
        }
        var errorEl = document.createElement("div");
        errorEl.className = "invalid-feedback d-block " + ERROR_CLASS;
        errorEl.textContent = ERROR_MESSAGE;
        container.appendChild(errorEl);
    }

    function validateInput(input, showMessage) {
        var value = (input.value || "").trim();
        if (!value) {
            clearError(input);
            return true;
        }
        var valid = isValidBirthDate(value);
        if (!valid && showMessage) {
            showError(input);
        } else if (valid) {
            clearError(input);
        }
        return valid;
    }

    function handleInput(event) {
        var input = event.target;
        var digitsBefore = countDigitsBefore(input.value, input.selectionStart || 0);
        var formatted = formatBirthDate(digitsOnly(input.value));
        input.value = formatted;
        var nextPos = cursorAfterDigits(formatted, digitsBefore);
        input.setSelectionRange(nextPos, nextPos);
        if (input.classList.contains("is-invalid")) {
            validateInput(input, false);
        }
    }

    function handleBlur(event) {
        var input = event.target;
        var value = (input.value || "").trim();
        if (!value) {
            clearError(input);
            return;
        }
        validateInput(input, true);
    }

    function initBirthDateInput(input) {
        if (!input || input.dataset.birthDateReady === "1") {
            return;
        }
        input.dataset.birthDateReady = "1";
        input.value = formatBirthDate(digitsOnly(input.value));

        input.addEventListener("input", handleInput);
        input.addEventListener("blur", handleBlur);

        var form = input.form;
        if (form && form.dataset.birthDateSubmitBound !== "1") {
            form.dataset.birthDateSubmitBound = "1";
            form.addEventListener("submit", function (event) {
                var fields = form.querySelectorAll("[data-birth-date-input]");
                var hasError = false;
                fields.forEach(function (field) {
                    if (!validateInput(field, true)) {
                        hasError = true;
                    }
                });
                if (hasError) {
                    event.preventDefault();
                }
            });
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-birth-date-input]").forEach(initBirthDateInput);
    });
})();
