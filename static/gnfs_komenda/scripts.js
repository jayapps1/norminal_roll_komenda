/* model for gh card */
document.getElementById("ghana_card").addEventListener("input", function (e) {
    let value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");

    if (!value.startsWith('GHA')) {
        value = 'GHA' + value.replace(/^GHA/, '');
    }

    let part1 = value.slice(0, 3);   // GHA
    let part2 = value.slice(3, 12);  // 9 digits
    let part3 = value.slice(12, 13); // last digit

    let formatted = part1;
    if (part2.length > 0) formatted += '-' + part2;
    if (part3.length > 0) formatted += '-' + part3;

    e.target.value = formatted;
});

/* endmodel for gh card */

/* bank number */
document.getElementById("account").addEventListener("input", function(e) {
    e.target.value = e.target.value.replace(/\D/g, ""); // remove non-digits
});
/* endbank number */

/* navbar */

 
  window.addEventListener("scroll", function () {
    const navbar = document.getElementById("mainNavbar");
    if (window.scrollY > 50) {
      navbar.classList.add("shrink");
    } else {
      navbar.classList.remove("shrink");
    }
  });


/* endnavbar */

/* delete model js */
function openModal(staffID, fullname) {
    const modal = document.getElementById('deleteModal');
    const form = document.getElementById('deleteForm');
    const text = document.getElementById('modalText');

    // Match your Django URL pattern
    form.action = '/delete/staff/' + staffID + '/';
    text.textContent = `Are you sure you want to delete ${fullname}?`;

    modal.style.display = 'block';
}

function closeModal() {
    document.getElementById('deleteModal').style.display = 'none';
}

// Close modal if clicking outside
window.onclick = function(event) {
    const modal = document.getElementById('deleteModal');
    if (event.target == modal) {
        closeModal();
    }
}
/* enddelete model js */

