/* Neural Strip — site menu.
 * Markup comes from tools/partials/nav.html (stamped into every page).
 * Without JavaScript the links render as a plain visible row. A one-line script in
 * <head> adds the "js" class before first paint, which turns the row into a menu
 * button, so there is no layout shift when this file loads.
 */
(function () {
    'use strict';
    var nav = document.querySelector('.site-nav');
    if (!nav) return;
    var btn = nav.querySelector('.nav-toggle');
    var panel = document.getElementById(btn.getAttribute('aria-controls'));
    var links = Array.prototype.slice.call(panel.querySelectorAll('a'));

    function isOpen() { return btn.getAttribute('aria-expanded') === 'true'; }

    function open() {
        btn.setAttribute('aria-expanded', 'true');
        nav.classList.add('is-open');
        var target = panel.querySelector('[aria-current="page"]') || links[0];
        if (target) target.focus();
    }

    function close(returnFocus) {
        if (!isOpen()) return;
        btn.setAttribute('aria-expanded', 'false');
        nav.classList.remove('is-open');
        if (returnFocus) btn.focus();
    }

    btn.addEventListener('click', function () {
        if (isOpen()) close(true); else open();
    });

    // Escape closes; Tab and Shift+Tab cycle between the button and the links while open.
    nav.addEventListener('keydown', function (e) {
        if (!isOpen()) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            close(true);
            return;
        }
        if (e.key !== 'Tab') return;
        var items = [btn].concat(links);
        var i = items.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) {
            e.preventDefault();
            items[items.length - 1].focus();
        } else if (!e.shiftKey && i === items.length - 1) {
            e.preventDefault();
            items[0].focus();
        }
    });

    // A click or tap outside the menu closes it without stealing focus.
    document.addEventListener('click', function (e) {
        if (isOpen() && !nav.contains(e.target)) close(false);
    });
})();
