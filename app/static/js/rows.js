document.querySelectorAll("tr[data-href]").forEach((row) => {
  row.addEventListener("click", (event) => {
    if (event.target.closest("a, button, input, select, textarea")) return;
    window.location.href = row.dataset.href;
  });
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter") window.location.href = row.dataset.href;
  });
});
