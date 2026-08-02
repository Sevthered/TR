// Replaces the inline on* attributes that used to be scattered across the
// templates. Two delegated listeners cover every case, and no markup carries
// executable code any more -- which is what lets the page ship a Content
// Security Policy without 'unsafe-inline' for scripts.
document.addEventListener('DOMContentLoaded', function () {
    // Delegated on document: works for markup rendered after load too.
    document.addEventListener('click', function (event) {
        var back = event.target.closest('[data-action="back"]');
        if (back) {
            event.preventDefault();
            window.history.back();
            return;
        }
        var nav = event.target.closest('[data-href]');
        if (nav) {
            event.preventDefault();
            window.location.href = nav.dataset.href;
        }
    });

    // Filter selects that submit their form on change. The attribute value is
    // an optional form id, for the selects that sit outside their own form.
    document.addEventListener('change', function (event) {
        var auto = event.target.closest('[data-autosubmit]');
        if (!auto) {
            return;
        }
        var formId = auto.getAttribute('data-autosubmit');
        var form = formId ? document.getElementById(formId) : auto.form;
        if (form) {
            form.submit();
        }
    });
});
