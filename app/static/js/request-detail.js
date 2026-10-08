(() => {
  const root = document.getElementById("request-detail");
  if (!root || !window.KB) return;
  const requestId = root.dataset.requestId;
  const requesterId = root.dataset.requesterId;
  const errorBanner = document.getElementById("request-error");
  const flash = document.getElementById("request-flash");
  const stored = sessionStorage.getItem("kb-flash");
  if (stored && flash) {
    flash.textContent = stored;
    flash.classList.remove("d-none");
    sessionStorage.removeItem("kb-flash");
  }

  function showError(message) {
    errorBanner.textContent = message || window.KB_REQUEST.genericError;
    errorBanner.classList.remove("d-none");
  }

  function clearError() {
    errorBanner.classList.add("d-none");
    errorBanner.textContent = "";
  }

  async function run(button, url, body) {
    clearError();
    const label = button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    if (button.dataset.working) button.textContent = button.dataset.working;
    try {
      const result = await window.KB.post(url, body);
      if (!result.ok) {
        const fields = result.data.fields || {};
        showError(fields.reason || result.data.message || window.KB_REQUEST.genericError);
        button.disabled = false;
        button.textContent = label;
        button.removeAttribute("aria-busy");
        return;
      }
      if (button.dataset.success) sessionStorage.setItem("kb-flash", button.dataset.success);
      window.location.reload();
    } catch {
      showError(window.KB_REQUEST.networkError);
      button.disabled = false;
      button.textContent = label;
      button.removeAttribute("aria-busy");
    }
  }

  function bindDialog(openId, dialogId) {
    const opener = document.getElementById(openId);
    const dialog = document.getElementById(dialogId);
    if (!opener || !dialog) return;
    opener.addEventListener("click", () => dialog.showModal());
    dialog.querySelectorAll("[data-close]").forEach((button) => {
      button.addEventListener("click", () => dialog.close());
    });
  }

  const approve = document.getElementById("approve");
  document.querySelectorAll('input[name="assignment"]').forEach((input) => {
    input.addEventListener("change", () => {
      const selected = document.querySelectorAll('input[name="assignment"]:checked').length;
      if (approve) approve.disabled = selected !== 1;
    });
  });
  if (approve) {
    approve.addEventListener("click", () => {
      const selected = document.querySelector('input[name="assignment"]:checked');
      if (!selected) return;
      run(approve, `/api/requests/${requestId}/approve`, { assignment_id: selected.value });
    });
  }

  bindDialog("reject-open", "reject-dialog");
  const rejectForm = document.getElementById("reject-form");
  if (rejectForm) {
    rejectForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const reason = document.getElementById("reject-reason").value.trim();
      const button = document.getElementById("reject-confirm");
      if (!reason) return;
      run(button, `/api/requests/${requestId}/reject`, { reason });
    });
  }

  const overrideForm = document.getElementById("override-form");
  if (overrideForm) {
    overrideForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const volunteerId = document.getElementById("override-volunteer").value;
      const reason = document.getElementById("override-reason").value.trim();
      if (!volunteerId || reason.length < 10) return;
      run(document.getElementById("override"), `/api/requests/${requestId}/override`, {
        volunteer_id: volunteerId,
        reason,
      });
    });
  }

  const retrigger = document.getElementById("retrigger");
  if (retrigger) {
    retrigger.addEventListener("click", () => {
      run(retrigger, `/api/requests/${requestId}/retrigger`, {});
    });
  }

  bindDialog("cancel-open", "cancel-dialog");
  const cancelForm = document.getElementById("cancel-form");
  if (cancelForm) {
    cancelForm.addEventListener("submit", (event) => {
      event.preventDefault();
      run(document.getElementById("cancel-confirm"), `/api/requests/${requestId}/cancel`, {});
    });
  }

  const exemptForm = document.getElementById("exempt-form");
  if (exemptForm) {
    exemptForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const volunteerId = document.getElementById("exempt-volunteer").value;
      const reason = document.getElementById("exempt-reason").value.trim();
      if (!volunteerId || !reason) return;
      run(document.getElementById("exempt"), "/api/exemptions", {
        volunteer_id: volunteerId,
        requester_id: requesterId,
        reason,
      });
    });
  }
})();
