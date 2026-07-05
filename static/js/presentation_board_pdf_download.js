import { encryptPDF } from "https://esm.sh/@pdfsmaller/pdf-encrypt@1.2.0";

const MIN_PASSWORD_LEN = 4;

function downloadBlob(bytes, filename) {
    const blob = new Blob([bytes], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename.toLowerCase().endsWith(".pdf") ? filename : `${filename}.pdf`;
    anchor.click();
    URL.revokeObjectURL(url);
}

function setFormBusy(form, busy) {
    const submitButton = form.querySelector('button[type="submit"]');
    if (!submitButton) {
        return;
    }
    if (!submitButton.dataset.defaultLabel) {
        submitButton.dataset.defaultLabel = submitButton.textContent;
    }
    submitButton.disabled = busy;
    submitButton.textContent = busy ? "처리 중…" : submitButton.dataset.defaultLabel;
}

document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("presentationBoardFileDownloadForm");
    if (!form) {
        return;
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const fetchUrl = form.dataset.fetchUrl || "";
        const passwordInput = document.getElementById("presentationBoardFilePassword");
        const nameEl = document.getElementById("presentationBoardFileDownloadName");
        const password = (passwordInput?.value || "").trim();

        if (password.length < MIN_PASSWORD_LEN) {
            window.alert(`암호는 ${MIN_PASSWORD_LEN}자 이상 입력해 주세요.`);
            return;
        }
        if (!fetchUrl) {
            window.alert("다운로드 URL을 찾을 수 없습니다.");
            return;
        }

        const label = nameEl?.textContent?.trim() || "download.pdf";
        setFormBusy(form, true);

        try {
            const response = await fetch(fetchUrl, { credentials: "same-origin" });
            if (!response.ok) {
                throw new Error(`fetch failed: ${response.status}`);
            }
            const pdfBytes = new Uint8Array(await response.arrayBuffer());
            const encrypted = await encryptPDF(pdfBytes, password);
            downloadBlob(encrypted, label);

            const modalEl = document.getElementById("presentationBoardFileDownloadModal");
            if (modalEl && window.bootstrap) {
                window.bootstrap.Modal.getInstance(modalEl)?.hide();
            }
        } catch (error) {
            console.error(error);
            window.alert(
                "PDF를 불러오거나 암호를 적용하지 못했습니다. 잠시 후 다시 시도해 주세요."
            );
        } finally {
            setFormBusy(form, false);
        }
    });
});
