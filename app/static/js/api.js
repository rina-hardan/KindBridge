window.KB = (() => {
  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)kb_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, data };
  }

  function clearErrors(form) {
    form.querySelectorAll(".is-invalid").forEach((el) => el.classList.remove("is-invalid"));
    form.querySelectorAll(".invalid-feedback").forEach((el) => { el.textContent = ""; });
  }

  function showErrors(form, banner, result) {
    const fields = result.data.fields || {};
    let shownInline = false;
    Object.entries(fields).forEach(([field, message]) => {
      const feedback = form.querySelector(`.invalid-feedback[data-field="${field}"]`);
      if (feedback) {
        feedback.textContent = message;
        feedback.parentElement.querySelector("input, select, textarea")?.classList.add("is-invalid");
        shownInline = true;
      }
    });
    if (!shownInline) {
      banner.textContent = result.data.message || window.KB_TEXT.genericError;
      banner.classList.remove("d-none");
    }
  }

  function bindForm(formId, url, buildBody, onSuccess) {
    const form = document.getElementById(formId);
    const banner = document.getElementById("form-error");
    const button = form.querySelector('button[type="submit"]');
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearErrors(form);
      banner.classList.add("d-none");
      document.getElementById("form-success")?.classList.add("d-none");
      button.disabled = true;
      try {
        const result = await post(url, buildBody(form));
        if (result.ok) {
          onSuccess(result.data);
        } else {
          showErrors(form, banner, result);
        }
      } catch {
        banner.textContent = window.KB_TEXT.networkError;
        banner.classList.remove("d-none");
      } finally {
        button.disabled = false;
      }
    });
  }

  return { post, bindForm };
})();
