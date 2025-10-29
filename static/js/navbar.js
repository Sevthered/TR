// static/js/navbar.js
// Placeholder search hook; no logo animation since image removed.

document.addEventListener('DOMContentLoaded', function () {
    const form = document.querySelector('.nav-search');
    if (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            const q = form.querySelector('.nav-search-input').value.trim();
            console.log('Search query (placeholder):', q);
            // implement your search action here (AJAX or redirect)
        });
    }
});
