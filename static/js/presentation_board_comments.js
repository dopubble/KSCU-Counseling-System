(function () {
    function bindPresentationCommentAccordion(root) {
        root.querySelectorAll(".presentation-comment-collapse").forEach(function (collapseEl) {
            var summary = root.querySelector(
                '[data-bs-target="#' + collapseEl.id + '"]'
            );
            if (!summary) {
                return;
            }
            var label = summary.querySelector(".presentation-comment-toggle-label");
            var icon = summary.querySelector(".presentation-comment-toggle-icon");
            collapseEl.addEventListener("shown.bs.collapse", function () {
                summary.setAttribute("aria-expanded", "true");
                if (label) {
                    label.textContent = "접기";
                }
                if (icon) {
                    icon.classList.replace("bi-chevron-down", "bi-chevron-up");
                }
            });
            collapseEl.addEventListener("hidden.bs.collapse", function () {
                summary.setAttribute("aria-expanded", "false");
                if (label) {
                    label.textContent = "내용 보기";
                }
                if (icon) {
                    icon.classList.replace("bi-chevron-up", "bi-chevron-down");
                }
            });
            summary.addEventListener("keydown", function (event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    bootstrap.Collapse.getOrCreateInstance(collapseEl).toggle();
                }
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        document
            .querySelectorAll(".presentation-board-comment-accordion")
            .forEach(bindPresentationCommentAccordion);

        var fileModal = document.getElementById("presentationBoardFileDownloadModal");
        if (fileModal) {
            fileModal.addEventListener("show.bs.modal", function (event) {
                var trigger = event.relatedTarget;
                if (!trigger) {
                    return;
                }
                var form = document.getElementById("presentationBoardFileDownloadForm");
                var nextInput = document.getElementById("presentationBoardFileDownloadNext");
                var nameEl = document.getElementById("presentationBoardFileDownloadName");
                var passwordInput = document.getElementById("presentationBoardFilePassword");
                if (form) {
                    form.action = trigger.getAttribute("data-file-action") || "";
                }
                if (nextInput) {
                    nextInput.value = window.location.pathname + window.location.search;
                }
                if (nameEl) {
                    nameEl.textContent = trigger.getAttribute("data-file-label") || "";
                }
                if (passwordInput) {
                    passwordInput.value = "";
                }
            });
        }
    });
})();
