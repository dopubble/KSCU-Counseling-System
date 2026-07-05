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

    function getPresentationRowChecks() {
        return Array.prototype.slice.call(
            document.querySelectorAll(".presentation-board-row-check")
        );
    }

    function getCheckedPresentationRows() {
        return getPresentationRowChecks().filter(function (input) {
            return input.checked;
        });
    }

    function syncPresentationSelectAllState() {
        var selectAll = document.getElementById("presentationBoardSelectAll");
        var checks = getPresentationRowChecks();
        if (!selectAll || !checks.length) {
            return;
        }
        var checkedCount = getCheckedPresentationRows().length;
        selectAll.checked = checkedCount === checks.length;
        selectAll.indeterminate = checkedCount > 0 && checkedCount < checks.length;
    }

    function bindPresentationBoardBulkDownload() {
        var selectAll = document.getElementById("presentationBoardSelectAll");
        var bulkBtn = document.getElementById("presentationBoardBulkDownloadBtn");
        var bulkModal = document.getElementById("presentationBoardBulkZipModal");
        var bulkForm = document.getElementById("presentationBoardBulkZipForm");
        var bulkIds = document.getElementById("presentationBoardBulkZipPostIds");
        var bulkCount = document.getElementById("presentationBoardBulkZipCount");
        var bulkPassword = document.getElementById("presentationBoardBulkZipPassword");
        var bulkNext = document.getElementById("presentationBoardBulkZipNext");

        getPresentationRowChecks().forEach(function (input) {
            input.addEventListener("change", syncPresentationSelectAllState);
        });

        if (selectAll) {
            selectAll.addEventListener("change", function () {
                getPresentationRowChecks().forEach(function (input) {
                    input.checked = selectAll.checked;
                });
                selectAll.indeterminate = false;
            });
        }

        if (bulkBtn && bulkModal && window.bootstrap) {
            bulkBtn.addEventListener("click", function () {
                var selected = getCheckedPresentationRows();
                if (!selected.length) {
                    window.alert("다운로드할 게시글을 하나 이상 선택해 주세요.");
                    return;
                }
                window.bootstrap.Modal.getOrCreateInstance(bulkModal).show();
            });
        }

        if (bulkModal) {
            bulkModal.addEventListener("show.bs.modal", function () {
                var selected = getCheckedPresentationRows();
                if (bulkIds) {
                    bulkIds.innerHTML = "";
                    selected.forEach(function (input) {
                        var hidden = document.createElement("input");
                        hidden.type = "hidden";
                        hidden.name = "post_ids";
                        hidden.value = input.value;
                        bulkIds.appendChild(hidden);
                    });
                }
                if (bulkCount) {
                    bulkCount.textContent = String(selected.length);
                }
                if (bulkPassword) {
                    bulkPassword.value = "";
                }
                if (bulkNext) {
                    bulkNext.value = window.location.pathname + window.location.search;
                }
            });
        }

        if (bulkForm) {
            bulkForm.addEventListener("submit", function () {
                window.setTimeout(function () {
                    if (bulkModal && window.bootstrap) {
                        window.bootstrap.Modal.getInstance(bulkModal)?.hide();
                    }
                }, 300);
            });
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document
            .querySelectorAll(".presentation-board-comment-accordion")
            .forEach(bindPresentationCommentAccordion);

        bindPresentationBoardBulkDownload();

        var fileModal = document.getElementById("presentationBoardFileDownloadModal");
        var downloadForm = document.getElementById("presentationBoardFileDownloadForm");
        if (fileModal) {
            fileModal.addEventListener("show.bs.modal", function (event) {
                var trigger = event.relatedTarget;
                if (!trigger) {
                    return;
                }
                var nextInput = document.getElementById("presentationBoardFileDownloadNext");
                var nameEl = document.getElementById("presentationBoardFileDownloadName");
                var passwordInput = document.getElementById("presentationBoardFilePassword");
                if (downloadForm) {
                    downloadForm.action =
                        trigger.getAttribute("data-file-download-url") || "";
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

        if (downloadForm) {
            downloadForm.addEventListener("submit", function () {
                window.setTimeout(function () {
                    if (fileModal && window.bootstrap) {
                        window.bootstrap.Modal.getInstance(fileModal)?.hide();
                    }
                }, 300);
            });
        }
    });
})();
