(() => {
  const base = "/plugins/open_tech_quest/assets/branding";
  document
    .querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"]')
    .forEach((link) => link.remove());
  for (const [rel, type, sizes, file] of [
    ["icon", "image/png", "32x32", "favicon-32.png"],
    ["icon", "image/svg+xml", "any", "favicon.svg"],
    ["apple-touch-icon", "image/png", "180x180", "apple-touch-icon.png"],
  ]) {
    const link = document.createElement("link");
    link.rel = rel;
    link.type = type;
    link.setAttribute("sizes", sizes);
    link.href = `${base}/${file}`;
    document.head.appendChild(link);
  }
})();

// Native account pages keep their form state; attendance is an explicit action in Quest.
(() => {
  function start() {
    if (
      location.pathname === "/quest" ||
      location.pathname.startsWith("/quest/") ||
      location.pathname === "/admin" ||
      location.pathname.startsWith("/admin/")
    )
      return;
    if (!Number(window.init?.userId)) return;
    let stopped = false,
      pending = false,
      notice = null,
      noticeGeneration = null,
      dismissedGeneration = null;
    function removeNotice() {
      notice?.remove();
      notice = null;
    }
    function stop() {
      stopped = true;
      clearInterval(interval);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", visible);
      removeNotice();
    }
    function render(state) {
      if (
        state?.require_attendance === false ||
        !state?.attendance?.enabled ||
        !state.can_ready ||
        state.ready_at ||
        dismissedGeneration === state.attendance.generation
      ) {
        removeNotice();
        return;
      }
      if (notice && noticeGeneration !== state.attendance.generation)
        removeNotice();
      if (notice) return;
      noticeGeneration = state.attendance.generation;
      notice = document.createElement("aside");
      notice.className = "quest-attendance-native";
      notice.setAttribute("aria-label", "Attendance check-in");
      const message = document.createElement("p");
      message.textContent = state.attendance.late_only
        ? "Late attendance check-in is open. Confirm in Quest, then ask an organizer about team admission."
        : "Attendance check-in is open. Confirm in Quest that you're at the event and participating.";
      const actions = document.createElement("div");
      const link = document.createElement("a");
      link.href = "/quest";
      link.textContent = "Confirm attendance in Quest";
      const dismiss = document.createElement("button");
      dismiss.type = "button";
      dismiss.textContent = "Not now";
      dismiss.addEventListener("click", () => {
        dismissedGeneration = state.attendance.generation;
        removeNotice();
      });
      actions.append(link, dismiss);
      notice.append(message, actions);
      document.body.appendChild(notice);
    }
    async function refresh() {
      if (stopped || pending || document.hidden) return;
      pending = true;
      try {
        const identity = await fetch("/api/v1/quest/bootstrap", {
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-Quest-Attendance-UI": "1" },
        });
        if (identity.status === 401 || identity.status === 403) {
          stop();
          return;
        }
        if (!identity.ok) return;
        const bootstrap = await identity.json();
        if (!bootstrap.success) return;
        if (bootstrap.data?.viewer?.role !== "participant") {
          stop();
          return;
        }
        const response = await fetch("/api/v1/quest/onboarding", {
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-Quest-Attendance-UI": "1" },
        });
        if (response.status === 401 || response.status === 403) {
          stop();
          return;
        }
        if (!response.ok) return;
        const envelope = await response.json();
        if (envelope.success && !stopped) render(envelope.data);
      } catch {
        /* A later focus or poll retries without interrupting the account form. */
      } finally {
        pending = false;
      }
    }
    function visible() {
      if (!document.hidden) void refresh();
    }
    const interval = window.setInterval(() => void refresh(), 5000);
    window.addEventListener("focus", refresh);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", visible);
    void refresh();
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
