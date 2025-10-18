// static/js/navbar.js
// Placeholder search hook; no logo animation since image removed.

document.addEventListener('DOMContentLoaded', function () {
    const form = document.querySelector('.nav-search');
    // Populate hidden course input from any element that has data-course-id
    try {
        var el = document.querySelector('[data-course-id]') || document.body;
        var courseId = el ? el.getAttribute('data-course-id') : null;
        var hiddenCourse = document.getElementById('nav-search-course');
        if (courseId && hiddenCourse) hiddenCourse.value = courseId;
    } catch (e) { }
    if (!form) return;

    const getValuesAndNavigate = function (e) {
        if (e && typeof e.preventDefault === 'function') e.preventDefault();
        const qinput = form.querySelector('.nav-search-input');
        const q = qinput ? qinput.value.trim() : '';
        const courseInput = document.getElementById('nav-search-course');
        const course = courseInput ? (courseInput.value || '') : '';
        if (!q && !course) return;
        const action = form.getAttribute('action') || window.location.pathname;
        const params = new URLSearchParams();
        if (q) params.set('q', q);
        if (course) params.set('course', course);
        const url = action + (params.toString() ? ('?' + params.toString()) : '');
        window.location.href = url;
    };

    form.addEventListener('submit', getValuesAndNavigate);
    const btn = form.querySelector('.nav-search-btn');
    if (btn) btn.addEventListener('click', getValuesAndNavigate);
    const qinput = form.querySelector('.nav-search-input');
    if (qinput) qinput.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); getValuesAndNavigate(ev); } });
});
