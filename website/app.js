/* Neural Strip — Shared JS */
/* Cartoon loader, archive nav, lightbox, share, GA4 events */
/* Voting temporarily disabled pending backend restoration. */

(function () {
    'use strict';

    let cartoons = [];
    let currentIndex = 0;
    let archiveExpanded = false;
    const ARCHIVE_PAGE_SIZE = 9;

    // ── Init ────────────────────────────────────────────

    async function init() {
        try {
            var resp = await fetch('cartoons.json');
            var data = await resp.json();
            cartoons = data.cartoons || [];
        } catch (e) {
            console.error('Failed to load cartoons.json:', e);
            cartoons = [];
        }

        if (cartoons.length === 0) return;

        updateMetaTags(cartoons[0]);
        renderHero(cartoons[0]);
        renderArchiveNav(cartoons[0]);
        renderGrid(cartoons);
        setupLightbox();
    }

    // ── Open Graph meta tags (for social sharing) ────────

    function updateMetaTags(cartoon) {
        var imgUrl = cartoon.image_url || '';
        var title = cartoon.headline || cartoon.setup || 'Neural Strip';
        var desc = cartoon.caption || 'AI Generated Humor about AI.';

        function setMeta(selector, value) {
            var el = document.querySelector(selector);
            if (el) el.setAttribute('content', value);
        }

        setMeta('meta[property="og:title"]', title);
        setMeta('meta[property="og:description"]', desc);
        setMeta('meta[property="og:image"]', imgUrl);
        setMeta('meta[name="twitter:title"]', title);
        setMeta('meta[name="twitter:description"]', desc);
        setMeta('meta[name="twitter:image"]', imgUrl);
    }

    // ── Archive Navigation (compact dropdown) ────────────

    function renderArchiveNav(selectedCartoon) {
        var nav = document.getElementById('archive-nav');
        if (!nav) return;

        // Parse selected date
        var parts = selectedCartoon.date.split('-');
        var selYear = parts[0];
        var selMonth = parts[1];
        var selDay = parts[2];

        // Build date index: { year: { month: [day, ...] } }
        var dateIndex = {};
        cartoons.forEach(function (c) {
            var p = c.date.split('-');
            if (!dateIndex[p[0]]) dateIndex[p[0]] = {};
            if (!dateIndex[p[0]][p[1]]) dateIndex[p[0]][p[1]] = [];
            dateIndex[p[0]][p[1]].push(p[2]);
        });

        var years = Object.keys(dateIndex).sort().reverse();
        var months = Object.keys(dateIndex[selYear] || {}).sort().reverse();
        var days = ((dateIndex[selYear] || {})[selMonth] || []).sort().reverse();

        var monthNames = ['', 'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December'];

        var html = '<span>Browse archive:</span>' +
            '<div class="archive-selects">' +
            '<select id="nav-year">' + years.map(function (y) {
                return '<option value="' + y + '"' + (y === selYear ? ' selected' : '') + '>' + y + '</option>';
            }).join('') + '</select>' +
            '<select id="nav-month">' + months.map(function (m) {
                return '<option value="' + m + '"' + (m === selMonth ? ' selected' : '') + '>' + monthNames[parseInt(m)] + '</option>';
            }).join('') + '</select>' +
            '<select id="nav-day">' + days.map(function (d) {
                return '<option value="' + d + '"' + (d === selDay ? ' selected' : '') + '>' + parseInt(d) + '</option>';
            }).join('') + '</select>' +
            '</div>';

        nav.innerHTML = html;

        function onNavChange() {
            var y = document.getElementById('nav-year').value;
            var m = document.getElementById('nav-month').value;
            var d = document.getElementById('nav-day').value;
            var dateStr = y + '-' + m + '-' + d;
            var cartoon = cartoons.find(function (c) { return c.date === dateStr; });
            if (cartoon) {
                renderHero(cartoon);
                renderArchiveNav(cartoon);
                renderGrid(cartoons);
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }

        // Year change: rebuild month + day
        document.getElementById('nav-year').addEventListener('change', function () {
            var y = this.value;
            var newMonths = Object.keys(dateIndex[y] || {}).sort().reverse();
            var mSel = document.getElementById('nav-month');
            mSel.innerHTML = newMonths.map(function (m) {
                return '<option value="' + m + '">' + monthNames[parseInt(m)] + '</option>';
            }).join('');
            mSel.dispatchEvent(new Event('change'));
        });

        // Month change: rebuild day
        document.getElementById('nav-month').addEventListener('change', function () {
            var y = document.getElementById('nav-year').value;
            var m = this.value;
            var newDays = ((dateIndex[y] || {})[m] || []).sort().reverse();
            var dSel = document.getElementById('nav-day');
            dSel.innerHTML = newDays.map(function (d) {
                return '<option value="' + d + '">' + parseInt(d) + '</option>';
            }).join('');
            onNavChange();
        });

        document.getElementById('nav-day').addEventListener('change', onNavChange);
    }

    // ── Hero ────────────────────────────────────────────

    function renderHero(cartoon) {
        var hero = document.getElementById('hero');
        if (!hero) return;

        updateMetaTags(cartoon);
        currentIndex = cartoons.indexOf(cartoon);
        if (currentIndex < 0) currentIndex = 0;

        hero.innerHTML =
            '<div class="hero-image-wrap" id="hero-image" title="Click to enlarge">' +
                imageOrPlaceholder(cartoon.image_url, cartoon.setup) +
            '</div>' +
            '<div class="hero-caption">' +
                '<div class="setup">' + esc(cartoon.setup) + '</div>' +
                '<div class="punchline">' + esc(cartoon.punchline) + '</div>' +
                (cartoon.source_url ? '<div class="source-link"><a href="' + esc(cartoon.source_url) + '" target="_blank" rel="noopener noreferrer" id="source-link">Read the story \u2192</a></div>' : '') +
            '</div>' +
            '<div class="hero-meta">' +
                '<span class="hero-date">' + esc(cartoon.date_display) + '</span>' +
                '<div class="share-wrap">' +
                    '<button class="share-btn" id="share-btn">Share</button>' +
                    '<div class="share-dropdown" id="share-dropdown">' +
                        '<button id="share-copy">Copy link</button>' +
                        '<a href="https://twitter.com/intent/tweet?text=' + encodeURIComponent(cartoon.caption) + '&url=' + encodeURIComponent('https://neuralstrip.com') + '" target="_blank" rel="noopener">Share on X</a>' +
                        '<a href="https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent('https://neuralstrip.com') + '" target="_blank" rel="noopener">Share on LinkedIn</a>' +
                        '<a href="https://api.whatsapp.com/send?text=' + encodeURIComponent(cartoon.caption + ' https://neuralstrip.com') + '" target="_blank" rel="noopener">Share on WhatsApp</a>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="hero-ig">' +
                '<a href="https://instagram.com/neural.strip" class="btn-instagram-subtle" target="_blank" rel="noopener">Follow @neural.strip on Instagram</a>' +
            '</div>';

        // Share handlers
        var shareBtn = document.getElementById('share-btn');
        var shareDropdown = document.getElementById('share-dropdown');
        if (shareBtn) {
            shareBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                shareDropdown.classList.toggle('open');
            });
        }

        var copyBtn = document.getElementById('share-copy');
        if (copyBtn) {
            copyBtn.addEventListener('click', function () {
                navigator.clipboard.writeText('https://neuralstrip.com').then(function () {
                    copyBtn.textContent = 'Copied!';
                    setTimeout(function () { copyBtn.textContent = 'Copy link'; }, 1500);
                });
                ga('cartoon_share', { method: 'copy_link', cartoon_id: cartoon.id });
            });
        }

        shareDropdown.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                var platform = link.textContent.trim().replace('Share on ', '').toLowerCase();
                ga('cartoon_share', { method: platform, cartoon_id: cartoon.id });
            });
        });

        document.addEventListener('click', function () {
            if (shareDropdown) shareDropdown.classList.remove('open');
        });

        // Source link GA4 tracking
        var sourceLink = document.getElementById('source-link');
        if (sourceLink) {
            sourceLink.addEventListener('click', function () {
                ga('source_click', { cartoon_id: cartoon.id, source_url: cartoon.source_url });
            });
        }

        var heroImg = document.getElementById('hero-image');
        if (heroImg) {
            heroImg.addEventListener('click', function () {
                openLightbox(currentIndex);
            });
        }

        document.querySelectorAll('.archive-card').forEach(function (card) {
            card.classList.toggle('active', card.dataset.id === cartoon.id);
        });
    }

    // ── Archive Grid (with pagination) ─────────────────

    function renderGrid(list) {
        var grid = document.getElementById('archive-grid');
        if (!grid) return;

        // Skip the first cartoon (shown in hero) for archive
        var archiveList = list.slice(1);
        var displayList = archiveExpanded ? archiveList : archiveList.slice(0, ARCHIVE_PAGE_SIZE);

        grid.innerHTML = displayList.map(function (c) {
            return '<div class="archive-card" data-id="' + esc(c.id) + '">' +
                '<div class="card-img">' + imageOrPlaceholder(c.image_url, c.setup) + '</div>' +
                '<div class="card-body">' +
                    '<div class="card-caption">' + esc(c.caption) + '</div>' +
                    '<div class="card-footer">' +
                        '<span>' + esc(c.date_display) + '</span>' +
                    '</div>' +
                '</div>' +
            '</div>';
        }).join('');

        grid.querySelectorAll('.archive-card').forEach(function (card) {
            card.addEventListener('click', function () {
                var cartoon = cartoons.find(function (c) { return c.id === card.dataset.id; });
                if (cartoon) {
                    renderHero(cartoon);
                    renderArchiveNav(cartoon);
                    renderGrid(cartoons);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        });

        // Toggle link
        var toggleWrap = document.getElementById('archive-toggle-wrap');
        if (toggleWrap) {
            if (archiveList.length > ARCHIVE_PAGE_SIZE) {
                var linkText = archiveExpanded
                    ? 'Show less \u2191'
                    : 'View all ' + archiveList.length + ' cartoons \u2192';
                toggleWrap.innerHTML = '<a href="#" id="archive-toggle">' + linkText + '</a>';
                document.getElementById('archive-toggle').addEventListener('click', function (e) {
                    e.preventDefault();
                    archiveExpanded = !archiveExpanded;
                    renderGrid(cartoons);
                });
            } else {
                toggleWrap.innerHTML = '';
            }
        }
    }

    // ── Lightbox ────────────────────────────────────────

    function setupLightbox() {
        var lb = document.getElementById('lightbox');
        if (!lb) return;

        lb.querySelector('.lb-close').addEventListener('click', closeLightbox);
        lb.querySelector('.lb-prev').addEventListener('click', function () { navLightbox(-1); });
        lb.querySelector('.lb-next').addEventListener('click', function () { navLightbox(1); });
        lb.addEventListener('click', function (e) { if (e.target === lb) closeLightbox(); });

        document.addEventListener('keydown', function (e) {
            if (!lb.classList.contains('open')) return;
            if (e.key === 'Escape') closeLightbox();
            if (e.key === 'ArrowLeft') navLightbox(-1);
            if (e.key === 'ArrowRight') navLightbox(1);
        });
    }

    function openLightbox(index) {
        var lb = document.getElementById('lightbox');
        if (!lb || !cartoons[index]) return;
        currentIndex = index;
        updateLightbox();
        lb.classList.add('open');
        document.body.style.overflow = 'hidden';
        ga('lightbox_open', { cartoon_id: cartoons[index].id });
    }

    function closeLightbox() {
        var lb = document.getElementById('lightbox');
        if (lb) {
            lb.classList.remove('open');
            document.body.style.overflow = '';
        }
    }

    function navLightbox(dir) {
        currentIndex = (currentIndex + dir + cartoons.length) % cartoons.length;
        updateLightbox();
    }

    function updateLightbox() {
        var lb = document.getElementById('lightbox');
        var c = cartoons[currentIndex];
        if (!lb || !c) return;

        var imgWrap = lb.querySelector('.lb-image-wrap');
        imgWrap.innerHTML = c.image_url
            ? '<img src="' + esc(c.image_url) + '" alt="' + esc(c.setup) + '" onerror="this.parentNode.innerHTML=\'<div class=lb-placeholder>&#x1f5bc;</div>\'">'
            : '<div class="lb-placeholder">&#x1f5bc;</div>';

        lb.querySelector('.lb-caption').textContent = c.setup;
        lb.querySelector('.lb-punchline').textContent = c.punchline;

        // Source link in lightbox
        var existingSource = lb.querySelector('.lb-source');
        if (existingSource) existingSource.remove();
        if (c.source_url) {
            var srcDiv = document.createElement('div');
            srcDiv.className = 'lb-source';
            srcDiv.innerHTML = '<a href="' + esc(c.source_url) + '" target="_blank" rel="noopener noreferrer">Read the story \u2192</a>';
            lb.querySelector('.lb-punchline').after(srcDiv);
        }
    }

    // ── Helpers ─────────────────────────────────────────

    function esc(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function imageOrPlaceholder(url, alt) {
        if (!url) return '<div class="img-placeholder">&#x1f5bc;</div>';
        return '<img src="' + esc(url) + '" alt="' + esc(alt) + '" loading="lazy" onerror="this.parentNode.innerHTML=\'<div class=img-placeholder>&#x1f5bc;</div>\'">';
    }

    function ga(event, params) {
        if (typeof gtag === 'function') {
            gtag('event', event, params || {});
        }
    }

    // ── Boot ────────────────────────────────────────────

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
