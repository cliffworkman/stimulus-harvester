(function () {
  "use strict";
  const detail = document.querySelector(".detail");
  if (!detail) return;
  const localId = detail.dataset.localId;

  function activeLabel() {
    const btn = document.querySelector(".label-buttons button.active");
    return btn ? btn.dataset.label : "unrated";
  }

  async function setLabel(label) {
    const notesEl = document.getElementById("notes");
    const clusterEl = document.getElementById("scene-cluster-id");
    const body = {
      label: label,
      notes: notesEl ? notesEl.value : null,
      scene_cluster_id: clusterEl && clusterEl.value ? clusterEl.value : null,
    };
    const res = await fetch(`/images/${localId}/label`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      document.querySelectorAll(".label-buttons button").forEach(function (b) {
        b.classList.toggle("active", b.dataset.label === label);
      });
    } else {
      alert("Failed to save (HTTP " + res.status + ")");
    }
  }

  document.querySelectorAll(".label-buttons button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setLabel(btn.dataset.label);
    });
  });

  const saveBtn = document.getElementById("save-notes");
  if (saveBtn) {
    saveBtn.addEventListener("click", function () {
      setLabel(activeLabel());
    });
  }

  document.addEventListener("keydown", function (event) {
    const tag = event.target.tagName;
    if (tag === "TEXTAREA" || tag === "INPUT") return;
    if (event.key === "a") setLabel("accepted");
    else if (event.key === "m") setLabel("maybe");
    else if (event.key === "r") setLabel("rejected");
  });
})();
