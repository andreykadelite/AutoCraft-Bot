(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
      return;
    }
    fn();
  }

  function formatDuration(totalSeconds) {
    var seconds = Math.max(0, Number(totalSeconds) || 0);
    var days = Math.floor(seconds / 86400);
    seconds -= days * 86400;
    var hours = Math.floor(seconds / 3600);
    seconds -= hours * 3600;
    var minutes = Math.floor(seconds / 60);
    seconds -= minutes * 60;

    var hh = String(hours).padStart(2, "0");
    var mm = String(minutes).padStart(2, "0");
    var ss = String(seconds).padStart(2, "0");
    if (days > 0) {
      return days + "д " + hh + ":" + mm + ":" + ss;
    }
    return hh + ":" + mm + ":" + ss;
  }

  function setupDelayChoices() {
    var options = Array.prototype.slice.call(
      document.querySelectorAll("[data-delay-option]")
    );
    if (!options.length) {
      return;
    }

    var customWrap = document.querySelector("[data-custom-time-wrap]");
    var customInput = document.querySelector("[data-custom-time-input]");

    function hasAnyChecked() {
      return options.some(function (input) {
        return !!input.checked;
      });
    }

    function applyCustomVisibility() {
      if (!customWrap) {
        return;
      }
      var selected = options.find(function (input) {
        return !!input.checked;
      });
      var isCustom = !!(
        selected &&
        selected.value &&
        String(selected.value).toLowerCase() === "custom"
      );
      customWrap.classList.toggle("hidden", !isCustom);
      if (!isCustom && customInput) {
        customInput.value = "";
      }
    }

    options.forEach(function (input) {
      input.addEventListener("change", function () {
        if (input.checked) {
          options.forEach(function (other) {
            if (other !== input) {
              other.checked = false;
            }
          });
        } else if (!hasAnyChecked()) {
          input.checked = true;
        }
        applyCustomVisibility();
      });
    });

    if (!hasAnyChecked()) {
      options[0].checked = true;
    }
    applyCustomVisibility();
  }

  function setupRecurringDaysValidation() {
    var form = document.querySelector(".power-recurring-form");
    if (!form) {
      return;
    }
    var days = Array.prototype.slice.call(
      form.querySelectorAll("[data-recurring-day]")
    );
    if (!days.length) {
      return;
    }

    function selectedCount() {
      var count = 0;
      days.forEach(function (input) {
        if (input.checked) {
          count += 1;
        }
      });
      return count;
    }

    function syncValidity() {
      var hasSelected = selectedCount() > 0;
      days.forEach(function (input) {
        input.setCustomValidity(
          hasSelected ? "" : "Выберите хотя бы один день недели."
        );
      });
    }

    days.forEach(function (input) {
      input.addEventListener("change", syncValidity);
    });
    form.addEventListener("reset", function () {
      window.setTimeout(syncValidity, 0);
    });
    form.addEventListener("submit", function (event) {
      syncValidity();
      if (!form.checkValidity()) {
        event.preventDefault();
        form.reportValidity();
      }
    });

    syncValidity();
  }

  function setupCountdown() {
    var countdown = document.querySelector("[data-power-countdown]");
    if (!countdown) {
      return;
    }
    var targetTs = Number(countdown.getAttribute("data-target-ts") || 0);
    if (!targetTs) {
      return;
    }

    function tick() {
      var now = Math.floor(Date.now() / 1000);
      var left = targetTs - now;
      if (left <= 0) {
        countdown.textContent = "Действие запускается...";
        return;
      }
      countdown.textContent = "До выполнения: " + formatDuration(left);
    }

    tick();
    window.setInterval(tick, 1000);
  }

  function setupActiveStatusPolling() {
    var holder = document.querySelector("[data-power-status-url]");
    if (!holder) {
      return;
    }
    var url = holder.getAttribute("data-power-status-url");
    var initialFingerprint =
      holder.getAttribute("data-power-active-fingerprint") || "";
    if (!url || !initialFingerprint) {
      return;
    }

    function readFingerprint(active) {
      if (!active || !active.id) {
        return "";
      }
      return (
        String(active.id) +
        ":" +
        String(active.status || "") +
        ":" +
        String(active.verification || "")
      );
    }

    function poll() {
      if (document.hidden) {
        return;
      }
      fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      })
        .then(function (resp) {
          if (!resp.ok) {
            throw new Error("status_bad_response");
          }
          return resp.json();
        })
        .then(function (data) {
          var nextFingerprint = readFingerprint((data || {}).active_action);
          if (nextFingerprint !== initialFingerprint) {
            window.location.reload();
          }
        })
        .catch(function () {
          // Silent fallback: keep current UI until manual refresh.
        });
    }

    window.setInterval(poll, 5000);
  }

  onReady(setupDelayChoices);
  onReady(setupRecurringDaysValidation);
  onReady(setupCountdown);
  onReady(setupActiveStatusPolling);
})();
