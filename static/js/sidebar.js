// static/js/sidebar.js
// Accessible collapsible sidebar sections with smooth height animation.

document.addEventListener('DOMContentLoaded', function () {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    const sections = Array.from(sidebar.querySelectorAll('.section'));

    function setBodyOpen(body, open) {
        if (open) {
            // from 0 -> scrollHeight then set to auto after transition
            body.style.height = body.scrollHeight + 'px';
            const onEnd = function () {
                body.style.height = 'auto';
                body.removeEventListener('transitionend', onEnd);
            };
            body.addEventListener('transitionend', onEnd);
        } else {
            // from auto or current -> explicit height -> 0
            body.style.height = body.scrollHeight + 'px';
            void body.offsetHeight; // force reflow
            body.style.height = '0px';
        }
    }

    sections.forEach(section => {
        const header = section.querySelector('.section-header');
        const body = section.querySelector('.section-body');
        const initiallyOpen = header.getAttribute('aria-expanded') === 'true' || section.classList.contains('open');

        if (initiallyOpen) {
            section.classList.add('open');
            header.setAttribute('aria-expanded', 'true');
            body.style.height = 'auto';
        } else {
            section.classList.remove('open');
            header.setAttribute('aria-expanded', 'false');
            body.style.height = '0px';
        }

        header.addEventListener('click', function () {
            const isOpen = header.getAttribute('aria-expanded') === 'true';
            if (isOpen) {
                header.setAttribute('aria-expanded', 'false');
                section.classList.remove('open');
                setBodyOpen(body, false);
            } else {
                // close other sections (single-open). Remove this block to allow multiple open.
                sections.forEach(s => {
                    if (s !== section) {
                        const h = s.querySelector('.section-header');
                        const b = s.querySelector('.section-body');
                        if (h && h.getAttribute('aria-expanded') === 'true') {
                            h.setAttribute('aria-expanded', 'false');
                            s.classList.remove('open');
                            setBodyOpen(b, false);
                        }
                    }
                });

                header.setAttribute('aria-expanded', 'true');
                section.classList.add('open');
                setBodyOpen(body, true);
            }

            // persist single open section id
            try {
                const open = sidebar.querySelector('.section.open');
                if (open) localStorage.setItem('sidebar-open-section', open.getAttribute('data-id') || '');
                else localStorage.removeItem('sidebar-open-section');
            } catch (e) { }
        });

        header.addEventListener('keydown', function (ev) {
            if (ev.key === ' ' || ev.key === 'Enter') {
                ev.preventDefault();
                header.click();
            }
        });
    });

    // restore persisted open section if present
    try {
        const key = 'sidebar-open-section';
        const saved = localStorage.getItem(key);
        if (saved) {
            const target = sidebar.querySelector('.section[data-id="' + saved + '"]');
            if (target) {
                sections.forEach(s => {
                    const h = s.querySelector('.section-header');
                    const b = s.querySelector('.section-body');
                    if (s === target) {
                        s.classList.add('open');
                        if (h) h.setAttribute('aria-expanded', 'true');
                        if (b) b.style.height = 'auto';
                    } else {
                        s.classList.remove('open');
                        if (h) h.setAttribute('aria-expanded', 'false');
                        if (b) b.style.height = '0px';
                    }
                });
            }
        }
    } catch (e) { }
});
