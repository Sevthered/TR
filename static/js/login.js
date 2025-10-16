// static/js/login.js
// Minimal animations: subtle focus pulse on inputs and shake card if required fields empty on submit.
// No extra features are added; IDs remain username and password.

document.addEventListener('DOMContentLoaded', function () {
    const form = document.querySelector('.login-form');
    const card = document.querySelector('.login-card');
    const inputUser = document.getElementById('username');
    const inputPass = document.getElementById('password');

    if (form) {
        form.addEventListener('submit', function (e) {
            // keep server submission intact; only block to animate if fields empty
            if (!inputUser.value.trim() || !inputPass.value.trim()) {
                e.preventDefault();
                if (card) {
                    card.animate([
                        { transform: 'translateX(0)' },
                        { transform: 'translateX(-8px)' },
                        { transform: 'translateX(8px)' },
                        { transform: 'translateX(0)' }
                    ], { duration: 300, easing: 'ease-in-out' });
                }
                // focus first empty field
                if (!inputUser.value.trim()) inputUser.focus();
                else inputPass.focus();
                return;
            }
            // allow normal POST when fields filled
        });
    }

    // subtle input focus pulse (purely visual)
    [inputUser, inputPass].forEach(input => {
        if (!input) return;
        input.addEventListener('focus', function () {
            input.style.transition = 'box-shadow 220ms cubic-bezier(.2,.9,.2,1), transform 220ms ease';
            input.style.boxShadow = '0 10px 30px rgba(124,92,255,0.08)';
            input.style.transform = 'translateY(-2px)';
        });
        input.addEventListener('blur', function () {
            input.style.boxShadow = '';
            input.style.transform = '';
        });
    });
});
