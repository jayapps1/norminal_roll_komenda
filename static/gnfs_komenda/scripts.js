/* Defensive Ghana Card Input Formatter */
document.addEventListener("DOMContentLoaded", function () {
    const ghCardInput = document.getElementById("ghana_card");
    if (ghCardInput) {
        ghCardInput.addEventListener("input", function (e) {
            let value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
            if (!value.startsWith('GHA')) {
                value = 'GHA' + value.replace(/^GHA/, '');
            }
            let part1 = value.slice(0, 3);
            let part2 = value.slice(3, 12);
            let part3 = value.slice(12, 13);
            let formatted = part1;
            if (part2.length > 0) formatted += '-' + part2;
            if (part3.length > 0) formatted += '-' + part3;
            e.target.value = formatted;
        });
    }

    const acctInput = document.getElementById("account");
    if (acctInput) {
        acctInput.addEventListener("input", function (e) {
            e.target.value = e.target.value.replace(/\D/g, "");
        });
    }

    const navbar = document.getElementById("mainNavbar");
    if (navbar) {
        window.addEventListener("scroll", function () {
            // Only add subtle shadow on scroll; never mutate layout dimensions or padding
            if (window.scrollY > 20) {
                navbar.classList.add("shadow");
            } else {
                navbar.classList.remove("shadow");
            }
        }, { passive: true });
    }

    // Sidebar Toggle
    const sidebarToggleBtn = document.getElementById("gnfsSidebarToggle");
    const sidebarBackdrop = document.getElementById("sidebarBackdrop");
    if (sidebarToggleBtn) {
        sidebarToggleBtn.addEventListener("click", function () {
            if (window.innerWidth < 992) {
                document.body.classList.toggle("sidebar-open");
            } else {
                document.body.classList.toggle("sidebar-collapsed");
            }
        });
    }
    if (sidebarBackdrop) {
        sidebarBackdrop.addEventListener("click", function () {
            document.body.classList.remove("sidebar-open");
        });
    }
});

/* Modal handler for Archival / Approval Request */
function openModal(staffID, fullname, canDirectArchive) {
    const modal = document.getElementById('deleteModal');
    const form = document.getElementById('deleteForm');
    const text = document.getElementById('modalText');
    const title = document.getElementById('modalTitle');

    if (!modal || !form || !text) return;

    form.action = '/delete/staff/' + staffID + '/';
    if (canDirectArchive) {
        if (title) title.textContent = "Confirm Personnel Archival";
        text.textContent = `Are you sure you want to soft-archive ${fullname} (${staffID})? The record will be deactivated.`;
    } else {
        if (title) title.textContent = "Request Personnel Archival";
        text.textContent = `Submit an official archival approval request for ${fullname} (${staffID}). This request will proceed through the DFO, Regional, and National approval chain.`;
    }

    modal.style.display = 'block';
}

function closeModal() {
    const modal = document.getElementById('deleteModal');
    if (modal) modal.style.display = 'none';
}

window.onclick = function (event) {
    const modal = document.getElementById('deleteModal');
    if (event.target == modal) {
        closeModal();
    }
};
