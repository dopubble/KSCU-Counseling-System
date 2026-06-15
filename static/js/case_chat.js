(function () {
    "use strict";

    function initCaseChat() {
        var root = document.getElementById("caseChatRoot");
        if (!root) return;

        var POLL_MS = 10000;
        var UNREAD_POLL_MS = 25000;
        var messagesUrl = root.dataset.messagesUrl;
        var sendUrl = root.dataset.sendUrl;
        var unreadUrl = root.dataset.unreadUrl;

        var toggleBtn = document.getElementById("caseChatToggleBtn");
        var closeBtn = document.getElementById("caseChatCloseBtn");
        var panel = document.getElementById("caseChatPanel");
        var messagesEl = document.getElementById("caseChatMessages");
        var form = document.getElementById("caseChatForm");
        var input = document.getElementById("caseChatInput");
        var unreadBadge = document.getElementById("caseChatUnreadBadge");

        if (!toggleBtn || !closeBtn || !panel || !messagesEl || !form || !input) {
            return;
        }

        var isOpen = false;
        var pollTimer = null;
        var unreadPollTimer = null;
        var lastMessageId = "";
        var knownIds = new Set();

        function isPageVisible() {
            return document.visibilityState === "visible";
        }

        function getCsrfToken() {
            var inputToken = form.querySelector("[name=csrfmiddlewaretoken]");
            if (inputToken && inputToken.value) return inputToken.value;
            var match = document.cookie.match(/csrftoken=([^;]+)/);
            return match ? decodeURIComponent(match[1]) : "";
        }

        function formatTime(isoString) {
            try {
                var date = new Date(isoString);
                return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
            } catch (e) {
                return "";
            }
        }

        function setUnreadBadgeVisible(visible) {
            if (!unreadBadge) return;
            unreadBadge.classList.toggle("d-none", !visible);
            unreadBadge.setAttribute("aria-hidden", visible ? "false" : "true");
        }

        function scrollToBottom() {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        function renderEmptyState() {
            if (messagesEl.children.length) return;
            var empty = document.createElement("p");
            empty.className = "case-chat-empty";
            empty.textContent = "대화를 시작해 보세요.";
            messagesEl.appendChild(empty);
        }

        function clearEmptyState() {
            var empty = messagesEl.querySelector(".case-chat-empty");
            if (empty) empty.remove();
        }

        function appendMessage(message) {
            if (!message || !message.id || knownIds.has(message.id)) return;
            knownIds.add(message.id);
            clearEmptyState();

            var bubble = document.createElement("div");
            bubble.className = "case-chat-bubble " + (message.is_mine ? "case-chat-bubble--mine" : "case-chat-bubble--theirs");
            bubble.dataset.messageId = message.id;

            var text = document.createElement("span");
            text.textContent = message.body;
            bubble.appendChild(text);

            var meta = document.createElement("span");
            meta.className = "case-chat-meta";
            meta.textContent = formatTime(message.created_at);
            bubble.appendChild(meta);

            messagesEl.appendChild(bubble);
            lastMessageId = message.id;
        }

        function fetchMessages(initial) {
            if (!isPageVisible()) return Promise.resolve();

            var url = messagesUrl;
            if (!initial && lastMessageId) {
                url += (url.indexOf("?") >= 0 ? "&" : "?") + "after=" + encodeURIComponent(lastMessageId);
            }

            return fetch(url, {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                credentials: "same-origin",
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("fetch failed");
                    return response.json();
                })
                .then(function (data) {
                    var list = data.messages || [];
                    if (initial) {
                        messagesEl.innerHTML = "";
                        knownIds.clear();
                        lastMessageId = "";
                    }
                    list.forEach(appendMessage);
                    if (initial) renderEmptyState();
                    scrollToBottom();
                    setUnreadBadgeVisible(false);
                })
                .catch(function () {
                    /* polling 실패는 조용히 무시 */
                });
        }

        function fetchUnread() {
            if (!unreadUrl || isOpen || !isPageVisible()) return Promise.resolve();

            return fetch(unreadUrl, {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                credentials: "same-origin",
            })
                .then(function (response) {
                    if (!response.ok) throw new Error("unread fetch failed");
                    return response.json();
                })
                .then(function (data) {
                    if (!isOpen && isPageVisible()) {
                        setUnreadBadgeVisible(Boolean(data.has_unread));
                    }
                })
                .catch(function () {
                    /* unread polling 실패는 조용히 무시 */
                });
        }

        function startPolling() {
            if (!isPageVisible()) return;
            stopPolling();
            fetchMessages(true);
            pollTimer = window.setInterval(function () {
                fetchMessages(false);
            }, POLL_MS);
        }

        function stopPolling() {
            if (pollTimer !== null) {
                window.clearInterval(pollTimer);
                pollTimer = null;
            }
        }

        function startUnreadPolling() {
            if (!isPageVisible()) return;
            stopUnreadPolling();
            fetchUnread();
            unreadPollTimer = window.setInterval(fetchUnread, UNREAD_POLL_MS);
        }

        function stopUnreadPolling() {
            if (unreadPollTimer !== null) {
                window.clearInterval(unreadPollTimer);
                unreadPollTimer = null;
            }
        }

        function syncPollingWithVisibility() {
            if (!isPageVisible()) {
                stopPolling();
                stopUnreadPolling();
                return;
            }

            if (isOpen) {
                startPolling();
            } else {
                startUnreadPolling();
            }
        }

        function focusChatInput() {
            window.requestAnimationFrame(function () {
                window.setTimeout(function () {
                    if (!isOpen || !input) return;
                    input.focus({ preventScroll: true });
                }, 0);
            });
        }

        function clearStaleModalOverlay() {
            if (document.querySelector(".modal.show")) return;
            document.querySelectorAll(".modal-backdrop").forEach(function (el) {
                el.remove();
            });
            document.body.classList.remove("modal-open");
            document.body.style.removeProperty("overflow");
            document.body.style.removeProperty("padding-right");
        }

        function openChat() {
            clearStaleModalOverlay();
            isOpen = true;
            setUnreadBadgeVisible(false);
            stopUnreadPolling();
            panel.classList.remove("d-none");
            panel.setAttribute("aria-hidden", "false");
            toggleBtn.setAttribute("aria-expanded", "true");
            if (isPageVisible()) {
                startPolling();
            }
            focusChatInput();
        }

        function closeChat() {
            isOpen = false;
            panel.classList.add("d-none");
            panel.setAttribute("aria-hidden", "true");
            toggleBtn.setAttribute("aria-expanded", "false");
            stopPolling();
            if (isPageVisible()) {
                startUnreadPolling();
            }
        }

        function toggleChat() {
            if (isOpen) closeChat();
            else openChat();
        }

        function submitChatForm() {
            if (typeof form.requestSubmit === "function") {
                form.requestSubmit();
                return;
            }
            form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
        }

        function showSendError(message) {
            var notice = panel.querySelector(".case-chat-send-error");
            if (!notice) {
                notice = document.createElement("p");
                notice.className = "case-chat-send-error text-danger small px-3 py-1 mb-0";
                notice.setAttribute("role", "alert");
                form.parentNode.insertBefore(notice, form);
            }
            notice.textContent = message;
            window.setTimeout(function () {
                if (notice.parentNode) notice.remove();
            }, 4000);
        }

        function sendMessage(body) {
            return fetch(sendUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRFToken": getCsrfToken(),
                },
                credentials: "same-origin",
                body: JSON.stringify({ body: body }),
            })
                .then(function (response) {
                    return response.json().then(function (data) {
                        if (!response.ok) throw new Error(data.error || "send failed");
                        return data.message;
                    });
                });
        }

        toggleBtn.addEventListener("click", toggleChat);
        closeBtn.addEventListener("click", closeChat);

        panel.addEventListener("click", function (event) {
            if (event.target === panel || event.target === messagesEl) {
                focusChatInput();
            }
        });

        form.addEventListener("submit", function (event) {
            event.preventDefault();
            var body = (input.value || "").trim();
            if (!body) return;

            input.value = "";
            input.disabled = true;
            sendMessage(body)
                .then(function (message) {
                    appendMessage(message);
                    scrollToBottom();
                })
                .catch(function () {
                    input.value = body;
                    showSendError("메시지 전송에 실패했습니다. 다시 시도해 주세요.");
                })
                .finally(function () {
                    input.disabled = false;
                    focusChatInput();
                });
        });

        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submitChatForm();
            }
        });

        document.addEventListener("visibilitychange", syncPollingWithVisibility);

        window.addEventListener("beforeunload", function () {
            stopPolling();
            stopUnreadPolling();
        });

        if (isPageVisible()) {
            startUnreadPolling();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initCaseChat);
    } else {
        initCaseChat();
    }
})();
