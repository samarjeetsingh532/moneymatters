// main.js — students will add JavaScript here as features are built

function downloadExportFile(url, button) {
    var errorBox = document.getElementById("export-error");
    var originalText = button.textContent;
    var loadingText = button.dataset.loadingText || "Loading…";

    if (errorBox) {
        errorBox.hidden = true;
        errorBox.textContent = "";
    }
    button.disabled = true;
    button.textContent = loadingText;

    fetch(url)
        .then(function (response) {
            if (!response.ok) {
                return response.json().then(function (data) {
                    throw new Error(data.error || "Could not generate the export.");
                });
            }
            var disposition = response.headers.get("Content-Disposition") || "";
            var match = disposition.match(/filename="?([^";]+)"?/);
            var filename = match ? match[1] : "export.xlsx";
            return response.blob().then(function (blob) {
                return { blob: blob, filename: filename };
            });
        })
        .then(function (result) {
            var blobUrl = URL.createObjectURL(result.blob);
            var link = document.createElement("a");
            link.href = blobUrl;
            link.download = result.filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(blobUrl);
        })
        .catch(function (err) {
            if (errorBox) {
                errorBox.textContent = err.message || "Export failed. Please try again.";
                errorBox.hidden = false;
            }
        })
        .finally(function () {
            button.disabled = false;
            button.textContent = originalText;
        });
}

function syncCurrencyToAccount(accountSelect, currencySelect) {
    if (!accountSelect || !currencySelect) return;
    var sync = function () {
        var selected = accountSelect.options[accountSelect.selectedIndex];
        var currency = selected ? selected.dataset.currency : null;
        if (currency) {
            currencySelect.value = currency;
        }
    };
    accountSelect.addEventListener("change", sync);
    sync();
}

function setupNavToggle(toggle, links) {
    if (!toggle || !links) return;
    toggle.addEventListener("click", function () {
        var isOpen = links.classList.toggle("nav-links-open");
        toggle.classList.toggle("nav-toggle-open", isOpen);
        toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
}

document.addEventListener("DOMContentLoaded", function () {
    setupNavToggle(
        document.getElementById("nav-toggle"),
        document.getElementById("nav-links")
    );

    syncCurrencyToAccount(
        document.querySelector(".account-select"),
        document.querySelector(".currency-select")
    );

    var monthlyBtn = document.getElementById("download-monthly-btn");
    if (monthlyBtn) {
        monthlyBtn.addEventListener("click", function () {
            var month = document.getElementById("export-month").value;
            var year = document.getElementById("export-year").value;
            var url = monthlyBtn.dataset.exportUrl + "?year=" + encodeURIComponent(year) +
                "&month=" + encodeURIComponent(month);
            downloadExportFile(url, monthlyBtn);
        });
    }

    var fullBtn = document.getElementById("download-full-btn");
    if (fullBtn) {
        fullBtn.addEventListener("click", function () {
            downloadExportFile(fullBtn.dataset.exportUrl, fullBtn);
        });
    }
});
