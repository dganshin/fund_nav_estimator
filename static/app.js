(function () {
  const refresh = document.body.dataset.refresh || "off";
  const seconds = Number(refresh);
  if (!seconds || Number.isNaN(seconds) || seconds <= 0) {
    return;
  }
  window.setTimeout(() => {
    window.location.reload();
  }, seconds * 1000);
})();
