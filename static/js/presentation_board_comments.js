(function () {
    function bindPresentationCommentEdit(root) {
        root.querySelectorAll(".presentation-comment-edit-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var body = btn.closest(".presentation-comment-body");
                if (!body) {
                    return;
                }
                var collapse = body.closest(".presentation-comment-collapse");
                if (collapse && !collapse.classList.contains("show") && window.bootstrap) {
                    window.bootstrap.Collapse.getOrCreateInstance(collapse).show();
                }
                body.querySelector(".presentation-comment-view")?.classList.add("d-none");
                body.querySelector(".presentation-comment-edit")?.classList.remove("d-none");
            });
        });
        root.querySelectorAll(".presentation-comment-edit-cancel").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var body = btn.closest(".presentation-comment-body");
                if (!body) {
                    return;
                }
                body.querySelector(".presentation-comment-view")?.classList.remove("d-none");
                body.querySelector(".presentation-comment-edit")?.classList.add("d-none");
            });
        });
    }

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

    function parseContentDispositionFilename(disposition, fallbackName) {
        if (!disposition) {
            return fallbackName || "download";
        }
        var starMatch = /filename\*=(?:UTF-8''|utf-8'')([^;\n]+)/i.exec(disposition);
        if (starMatch) {
            try {
                return decodeURIComponent(starMatch[1]);
            } catch (error) {
                return starMatch[1];
            }
        }
        var quotedMatch = /filename="([^"]+)"/i.exec(disposition);
        if (quotedMatch) {
            return quotedMatch[1];
        }
        return fallbackName || "download";
    }

    function triggerBlobDownload(blob, filename) {
        var url = window.URL.createObjectURL(blob);
        var anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        anchor.style.display = "none";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(function () {
            window.URL.revokeObjectURL(url);
        }, 0);
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
            bulkForm.addEventListener("submit", function (event) {
                event.preventDefault();
                var submitBtn = bulkForm.querySelector('button[type="submit"]');
                if (submitBtn) {
                    submitBtn.disabled = true;
                }

                fetch(bulkForm.action, {
                    method: "POST",
                    body: new FormData(bulkForm),
                    credentials: "same-origin",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                    },
                })
                    .then(function (response) {
                        var contentType = response.headers.get("Content-Type") || "";
                        if (response.ok && contentType.indexOf("application/zip") !== -1) {
                            var filename = parseContentDispositionFilename(
                                response.headers.get("Content-Disposition") || "",
                                "presentation_reports.zip"
                            );
                            return response.blob().then(function (blob) {
                                return { blob: blob, filename: filename };
                            });
                        }
                        return response.text().then(function (text) {
                            var message =
                                (text || "").trim() ||
                                "ZIP 파일을 받지 못했습니다. 잠시 후 다시 시도해 주세요.";
                            throw new Error(message);
                        });
                    })
                    .then(function (payload) {
                        triggerBlobDownload(payload.blob, payload.filename);
                        if (bulkModal && window.bootstrap) {
                            window.bootstrap.Modal.getInstance(bulkModal)?.hide();
                        }
                    })
                    .catch(function (error) {
                        window.alert(
                            error && error.message
                                ? error.message
                                : "다운로드에 실패했습니다."
                        );
                    })
                    .finally(function () {
                        if (submitBtn) {
                            submitBtn.disabled = false;
                        }
                    });
            });
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document
            .querySelectorAll(".presentation-board-comment-accordion")
            .forEach(function (root) {
                bindPresentationCommentAccordion(root);
                bindPresentationCommentEdit(root);
            });

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
            downloadForm.addEventListener("submit", function (event) {
                event.preventDefault();
                var submitBtn = downloadForm.querySelector('button[type="submit"]');
                var nameEl = document.getElementById("presentationBoardFileDownloadName");
                var fallbackFilename =
                    (nameEl && nameEl.textContent && nameEl.textContent.trim()) ||
                    "download.pdf";
                if (submitBtn) {
                    submitBtn.disabled = true;
                }

                fetch(downloadForm.action, {
                    method: "POST",
                    body: new FormData(downloadForm),
                    credentials: "same-origin",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                    },
                })
                    .then(function (response) {
                        var contentType = response.headers.get("Content-Type") || "";
                        if (response.ok && contentType.indexOf("application/pdf") !== -1) {
                            var filename = parseContentDispositionFilename(
                                response.headers.get("Content-Disposition") || "",
                                fallbackFilename
                            );
                            return response.blob().then(function (blob) {
                                return { blob: blob, filename: filename };
                            });
                        }
                        return response.text().then(function (text) {
                            var message =
                                (text || "").trim() ||
                                "PDF 파일을 받지 못했습니다. 잠시 후 다시 시도해 주세요.";
                            throw new Error(message);
                        });
                    })
                    .then(function (payload) {
                        triggerBlobDownload(payload.blob, payload.filename);
                        if (fileModal && window.bootstrap) {
                            window.bootstrap.Modal.getInstance(fileModal)?.hide();
                        }
                    })
                    .catch(function (error) {
                        window.alert(
                            error && error.message
                                ? error.message
                                : "다운로드에 실패했습니다."
                        );
                    })
                    .finally(function () {
                        if (submitBtn) {
                            submitBtn.disabled = false;
                        }
                    });
            });
        }
    });
})();
