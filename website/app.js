/* Neural Strip — Shared JS */
/* Cartoon loader, calendar, lightbox, share, voting, GA4 events */

(function () {
    'use strict';

    let cartoons = [];
    let currentIndex = 0;

    // ── Init ────────────────────────────────────────────

    async function init() {
        try {
            const resp = await fetch('cartoons.json');
            const data = await resp.json();
            cartoons = data.cartoons || [];
        } catch (e) {
            console.error('Failed to load cartoons.json:', e);
            cartoons = [];
        }

        if (cartoons.length === 0) return;

        renderHero(cartoons[0]);
        renderGrid(cartoons);

        const now = new Date();
        renderCalendar(now.getFullYear(), now.getMonth());
        renderCalendarModal(now.getFullYear(), now.getMonth());

        setupLightbox();
        setupMobileCalendar();
    }

    // ── Hero ────────────────────────────────────────────

    function renderHero(cartoon) {
        const hero = document.getElementById('hero');
        if (!hero) return;

        currentIndex = cartoons.indexOf(cartoon);
        if (currentIndex < 0) currentIndex = 0;

        const votes = getVotes(cartoon.id);
        const voted = getVoted(cartoon.id);

        hero.innerHTML = `
            <div class="hero-image-wrap" id="hero-image" title="Click to enlarge">
                ${imageOrPlaceholder(cartoon.image_url, cartoon.setup)}
            </div>
            <div class="hero-caption">
                <div class="setup">${esc(cartoon.setup)}</div>
                <div class="punchline">${esc(cartoon.punchline)}</div>
            </div>
            <div class="hero-meta">
                <span class="hero-date">${esc(cartoon.date_display)}</span>
                <button class="vote-btn${voted === 'like' ? ' voted' : ''}" id="like-btn"
                    ${voted ? 'disabled' : ''}>&#x1f44d; ${votes.likes}</button>
                <button class="vote-btn${voted === 'dislike' ? ' voted' : ''}" id="dislike-btn"
                    ${voted ? 'disabled' : ''}>&#x1f44e; ${votes.dislikes}</button>
                <div class="share-wrap">
                    <button class="share-btn" id="share-btn">Share</button>
                    <div class="share-dropdown" id="share-dropdown">
                        <button id="share-copy">Copy link</button>
                        <a href="https://twitter.com/intent/tweet?text=${encodeURIComponent(cartoon.caption)}&url=${encodeURIComponent('https://neuralstrip.com')}" target="_blank" rel="noopener">Share on X</a>
                        <a href="https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent('https://neuralstrip.com')}" target="_blank" rel="noopener">Share on LinkedIn</a>
                        <a href="https://api.whatsapp.com/send?text=${encodeURIComponent(cartoon.caption + ' https://neuralstrip.com')}" target="_blank" rel="noopener">Share on WhatsApp</a>
                        <button id="share-download" data-url="${esc(cartoon.image_url)}" data-name="${esc(cartoon.id)}.jpg">Download image</button>
                    </div>
                </div>
            </div>
        `;

        // Vote handlers
        const likeBtn = document.getElementById('like-btn');
        const dislikeBtn = document.getElementById('dislike-btn');

        if (likeBtn && !voted) {
            likeBtn.addEventListener('click', function () {
                vote(cartoon.id, 'like');
                renderHero(cartoon);
                ga('cartoon_like', { cartoon_id: cartoon.id });
            });
        }
        if (dislikeBtn && !voted) {
            dislikeBtn.addEventListener('click', function () {
                vote(cartoon.id, 'dislike');
                renderHero(cartoon);
                ga('cartoon_dislike', { cartoon_id: cartoon.id });
            });
        }

        // Share handlers
        const shareBtn = document.getElementById('share-btn');
        const shareDropdown = document.getElementById('share-dropdown');
        if (shareBtn) {
            shareBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                shareDropdown.classList.toggle('open');
            });
        }

        const copyBtn = document.getElementById('share-copy');
        if (copyBtn) {
            copyBtn.addEventListener('click', function () {
                navigator.clipboard.writeText('https://neuralstrip.com').then(function () {
                    copyBtn.textContent = 'Copied!';
                    setTimeout(function () { copyBtn.textContent = 'Copy link'; }, 1500);
                });
                ga('cartoon_share', { method: 'copy_link', cartoon_id: cartoon.id });
            });
        }

        const dlBtn = document.getElementById('share-download');
        if (dlBtn) {
            dlBtn.addEventListener('click', function () {
                const a = document.createElement('a');
                a.href = dlBtn.dataset.url;
                a.download = dlBtn.dataset.name;
                a.click();
                ga('cartoon_share', { method: 'download', cartoon_id: cartoon.id });
            });
        }

        // Track social shares
        shareDropdown.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                const platform = link.textContent.trim().replace('Share on ', '').toLowerCase();
                ga('cartoon_share', { method: platform, cartoon_id: cartoon.id });
            });
        });

        // Close dropdown on outside click
        document.addEventListener('click', function () {
            if (shareDropdown) shareDropdown.classList.remove('open');
        });

        // Lightbox on hero image click
        const heroImg = document.getElementById('hero-image');
        if (heroImg) {
            heroImg.addEventListener('click', function () {
                openLightbox(currentIndex);
            });
        }

        // Highlight active card
        document.querySelectorAll('.archive-card').forEach(function (card) {
            card.classList.toggle('active', card.dataset.id === cartoon.id);
        });
    }

    // ── Archive Grid ────────────────────────────────────

    function renderGrid(list) {
        const grid = document.getElementById('archive-grid');
        if (!grid) return;

        grid.innerHTML = list.map(function (c) {
            const votes = getVotes(c.id);
            return `
                <div class="archive-card" data-id="${esc(c.id)}">
                    <div class="card-img">
                        ${imageOrPlaceholder(c.image_url, c.setup)}
                    </div>
                    <div class="card-body">
                        <div class="card-caption">${esc(c.caption)}</div>
                        <div class="card-footer">
                            <span>${esc(c.date_display)}</span>
                            <span>&#x1f44d;${votes.likes} &#x1f44e;${votes.dislikes}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        grid.querySelectorAll('.archive-card').forEach(function (card) {
            card.addEventListener('click', function () {
                const cartoon = cartoons.find(function (c) { return c.id === card.dataset.id; });
                if (cartoon) {
                    renderHero(cartoon);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        });
    }

    // ── Calendar ────────────────────────────────────────

    function renderCalendar(year, month) {
        const el = document.getElementById('calendar-desktop');
        if (el) renderCalendarInto(el, year, month);
    }

    function renderCalendarModal(year, month) {
        const el = document.getElementById('calendar-modal-content');
        if (el) renderCalendarInto(el, year, month);
    }

    function renderCalendarInto(container, year, month) {
        const cartoonDates = new Set(cartoons.map(function (c) { return c.date; }));
        const today = new Date();
        const todayStr = today.toISOString().slice(0, 10);

        const firstDay = new Date(year, month, 1).getDay();
        const daysInMonth = new Date(year, month + 1, 0).getDate();
        const monthName = new Date(year, month, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

        let html = `
            <div class="calendar">
                <div class="calendar-header">
                    <button class="cal-prev" data-y="${year}" data-m="${month}">&lsaquo;</button>
                    <span class="month-label">${monthName}</span>
                    <button class="cal-next" data-y="${year}" data-m="${month}">&rsaquo;</button>
                </div>
                <div class="calendar-grid">
                    <span class="day-label">S</span><span class="day-label">M</span>
                    <span class="day-label">T</span><span class="day-label">W</span>
                    <span class="day-label">T</span><span class="day-label">F</span>
                    <span class="day-label">S</span>
        `;

        for (let i = 0; i < firstDay; i++) {
            html += '<span class="day"></span>';
        }

        for (let d = 1; d <= daysInMonth; d++) {
            const dateStr = year + '-' + String(month + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
            const hasCartoon = cartoonDates.has(dateStr);
            const isToday = dateStr === todayStr;
            let cls = 'day';
            if (hasCartoon) cls += ' has-cartoon';
            if (isToday) cls += ' today';

            html += `<span class="${cls}" ${hasCartoon ? 'data-date="' + dateStr + '"' : ''}>${d}</span>`;
        }

        html += '</div></div>';
        container.innerHTML = html;

        // Nav handlers
        container.querySelectorAll('.cal-prev').forEach(function (btn) {
            btn.addEventListener('click', function () {
                let m = parseInt(btn.dataset.m) - 1;
                let y = parseInt(btn.dataset.y);
                if (m < 0) { m = 11; y--; }
                renderCalendarInto(container, y, m);
            });
        });

        container.querySelectorAll('.cal-next').forEach(function (btn) {
            btn.addEventListener('click', function () {
                let m = parseInt(btn.dataset.m) + 1;
                let y = parseInt(btn.dataset.y);
                if (m > 11) { m = 0; y++; }
                renderCalendarInto(container, y, m);
            });
        });

        // Day click
        container.querySelectorAll('.day.has-cartoon').forEach(function (day) {
            day.addEventListener('click', function () {
                const cartoon = cartoons.find(function (c) { return c.date === day.dataset.date; });
                if (cartoon) {
                    renderHero(cartoon);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                    // Close modal if open
                    const modal = document.getElementById('calendar-modal');
                    if (modal) modal.classList.remove('open');
                }
            });
        });
    }

    function setupMobileCalendar() {
        const btn = document.getElementById('calendar-mobile-trigger');
        const modal = document.getElementById('calendar-modal');
        const closeBtn = document.getElementById('calendar-modal-close');

        if (btn && modal) {
            btn.addEventListener('click', function () {
                modal.classList.add('open');
                const now = new Date();
                renderCalendarModal(now.getFullYear(), now.getMonth());
            });
        }
        if (closeBtn && modal) {
            closeBtn.addEventListener('click', function () { modal.classList.remove('open'); });
        }
        if (modal) {
            modal.addEventListener('click', function (e) {
                if (e.target === modal) modal.classList.remove('open');
            });
        }
    }

    // ── Lightbox ────────────────────────────────────────

    function setupLightbox() {
        const lb = document.getElementById('lightbox');
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
        const lb = document.getElementById('lightbox');
        if (!lb || !cartoons[index]) return;
        currentIndex = index;
        updateLightbox();
        lb.classList.add('open');
        document.body.style.overflow = 'hidden';
        ga('lightbox_open', { cartoon_id: cartoons[index].id });
    }

    function closeLightbox() {
        const lb = document.getElementById('lightbox');
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
        const lb = document.getElementById('lightbox');
        const c = cartoons[currentIndex];
        if (!lb || !c) return;

        const imgWrap = lb.querySelector('.lb-image-wrap');
        imgWrap.innerHTML = c.image_url
            ? '<img src="' + esc(c.image_url) + '" alt="' + esc(c.setup) + '" onerror="this.parentNode.innerHTML=\'<div class=lb-placeholder>&#x1f5bc;</div>\'">'
            : '<div class="lb-placeholder">&#x1f5bc;</div>';

        lb.querySelector('.lb-caption').textContent = c.setup;
        lb.querySelector('.lb-punchline').textContent = c.punchline;
    }

    // ── Voting (localStorage) ───────────────────────────

    function getVotes(id) {
        try {
            const data = JSON.parse(localStorage.getItem('ns_votes_' + id) || '{}');
            return { likes: data.likes || 0, dislikes: data.dislikes || 0 };
        } catch (e) { return { likes: 0, dislikes: 0 }; }
    }

    function getVoted(id) {
        try { return localStorage.getItem('ns_voted_' + id) || ''; }
        catch (e) { return ''; }
    }

    function vote(id, type) {
        const votes = getVotes(id);
        if (type === 'like') votes.likes++;
        else votes.dislikes++;
        try {
            localStorage.setItem('ns_votes_' + id, JSON.stringify(votes));
            localStorage.setItem('ns_voted_' + id, type);
        } catch (e) { /* quota */ }
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
