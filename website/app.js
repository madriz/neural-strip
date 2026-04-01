/* Neural Strip — Shared JS */
/* Cartoon loader, calendar, lightbox, share, Supabase voting, GA4 events */

(function () {
    'use strict';

    // ── Supabase config ──────────────────────────────────
    const SUPABASE_URL = 'https://***REMOVED***.supabase.co';
    const SUPABASE_ANON_KEY = '***REMOVED***';

    let supabaseClient = null;
    let voteCounts = {}; // { cartoon_id: { likes: N, dislikes: N } }

    function getVisitorId() {
        var id = localStorage.getItem('ns_visitor_id');
        if (!id) {
            id = crypto.randomUUID ? crypto.randomUUID() : 'v-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
            localStorage.setItem('ns_visitor_id', id);
        }
        return id;
    }

    async function initSupabase() {
        if (typeof window.supabase === 'undefined') return;
        try {
            supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
            // Fetch vote counts
            var { data } = await supabaseClient.rpc('ns_vote_counts');
            if (!data) {
                // Fallback: manual query
                var { data: rows } = await supabaseClient
                    .from('ns_votes')
                    .select('cartoon_id, vote');
                if (rows) {
                    rows.forEach(function (r) {
                        if (!voteCounts[r.cartoon_id]) voteCounts[r.cartoon_id] = { likes: 0, dislikes: 0 };
                        if (r.vote === 'like') voteCounts[r.cartoon_id].likes++;
                        else voteCounts[r.cartoon_id].dislikes++;
                    });
                }
            } else {
                data.forEach(function (r) {
                    voteCounts[r.cartoon_id] = { likes: r.likes || 0, dislikes: r.dislikes || 0 };
                });
            }
        } catch (e) {
            console.warn('Supabase init failed, using localStorage fallback:', e);
        }
    }

    let cartoons = [];
    let currentIndex = 0;
    let cartoonMonths = [];

    // ── Init ────────────────────────────────────────────

    async function init() {
        await initSupabase();

        try {
            var resp = await fetch('cartoons.json');
            var data = await resp.json();
            cartoons = data.cartoons || [];
        } catch (e) {
            console.error('Failed to load cartoons.json:', e);
            cartoons = [];
        }

        if (cartoons.length === 0) return;

        cartoonMonths = getCartoonMonths();
        renderHero(cartoons[0]);
        renderGrid(cartoons);

        if (cartoonMonths.length > 0) {
            renderCalendarSection(cartoonMonths[0].year, cartoonMonths[0].month);
        }

        setupLightbox();
    }

    // ── Cartoon months ──────────────────────────────────

    function getCartoonMonths() {
        var seen = {};
        var months = [];
        cartoons.forEach(function (c) {
            var parts = c.date.split('-');
            var key = parts[0] + '-' + parts[1];
            if (!seen[key]) {
                seen[key] = true;
                months.push({ year: parseInt(parts[0]), month: parseInt(parts[1]) - 1 });
            }
        });
        months.sort(function (a, b) {
            return b.year !== a.year ? b.year - a.year : b.month - a.month;
        });
        return months;
    }

    function findMonthIndex(year, month) {
        for (var i = 0; i < cartoonMonths.length; i++) {
            if (cartoonMonths[i].year === year && cartoonMonths[i].month === month) return i;
        }
        return -1;
    }

    // ── Hero ────────────────────────────────────────────

    function renderHero(cartoon) {
        var hero = document.getElementById('hero');
        if (!hero) return;

        currentIndex = cartoons.indexOf(cartoon);
        if (currentIndex < 0) currentIndex = 0;

        var votes = getVotes(cartoon.id);
        var voted = getVoted(cartoon.id);

        hero.innerHTML =
            '<div class="hero-image-wrap" id="hero-image" title="Click to enlarge">' +
                imageOrPlaceholder(cartoon.image_url, cartoon.setup) +
            '</div>' +
            '<div class="hero-caption">' +
                '<div class="setup">' + esc(cartoon.setup) + '</div>' +
                '<div class="punchline">' + esc(cartoon.punchline) + '</div>' +
            '</div>' +
            '<div class="hero-meta">' +
                '<span class="hero-date">' + esc(cartoon.date_display) + '</span>' +
                '<button class="vote-btn' + (voted === 'like' ? ' voted' : '') + '" id="like-btn"' +
                    (voted ? ' disabled' : '') + '>&#x1f44d; ' + votes.likes + '</button>' +
                '<button class="vote-btn' + (voted === 'dislike' ? ' voted' : '') + '" id="dislike-btn"' +
                    (voted ? ' disabled' : '') + '>&#x1f44e; ' + votes.dislikes + '</button>' +
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

        // Vote handlers
        var likeBtn = document.getElementById('like-btn');
        var dislikeBtn = document.getElementById('dislike-btn');

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

    // ── Archive Grid ────────────────────────────────────

    function renderGrid(list) {
        var grid = document.getElementById('archive-grid');
        if (!grid) return;

        grid.innerHTML = list.map(function (c) {
            var votes = getVotes(c.id);
            return '<div class="archive-card" data-id="' + esc(c.id) + '">' +
                '<div class="card-img">' + imageOrPlaceholder(c.image_url, c.setup) + '</div>' +
                '<div class="card-body">' +
                    '<div class="card-caption">' + esc(c.caption) + '</div>' +
                    '<div class="card-footer">' +
                        '<span>' + esc(c.date_display) + '</span>' +
                        '<span>&#x1f44d;' + votes.likes + ' &#x1f44e;' + votes.dislikes + '</span>' +
                    '</div>' +
                '</div>' +
            '</div>';
        }).join('');

        grid.querySelectorAll('.archive-card').forEach(function (card) {
            card.addEventListener('click', function () {
                var cartoon = cartoons.find(function (c) { return c.id === card.dataset.id; });
                if (cartoon) {
                    renderHero(cartoon);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        });
    }

    // ── Calendar Section ────────────────────────────────

    function renderCalendarSection(year, month) {
        var section = document.getElementById('calendar-section');
        if (!section) return;

        var cartoonDates = {};
        cartoons.forEach(function (c) { cartoonDates[c.date] = true; });

        var today = new Date();
        var todayStr = today.toISOString().slice(0, 10);
        var mi = findMonthIndex(year, month);

        var firstDay = new Date(year, month, 1).getDay();
        var daysInMonth = new Date(year, month + 1, 0).getDate();
        var monthName = new Date(year, month, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

        var jumpHtml = '<div class="calendar-jump"><select id="calendar-jump">';
        cartoonMonths.forEach(function (cm) {
            var label = new Date(cm.year, cm.month, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
            var sel = (cm.year === year && cm.month === month) ? ' selected' : '';
            jumpHtml += '<option value="' + cm.year + '-' + cm.month + '"' + sel + '>' + label + '</option>';
        });
        jumpHtml += '</select></div>';

        var hasPrev = mi >= 0 && mi < cartoonMonths.length - 1;
        var hasNext = mi > 0;

        var html = jumpHtml +
            '<div class="calendar">' +
                '<div class="calendar-header">' +
                    '<button id="cal-prev"' + (hasPrev ? '' : ' disabled') + '>&lsaquo;</button>' +
                    '<span class="month-label">' + monthName + '</span>' +
                    '<button id="cal-next"' + (hasNext ? '' : ' disabled') + '>&rsaquo;</button>' +
                '</div>' +
                '<div class="calendar-grid">' +
                    '<span class="day-label">S</span><span class="day-label">M</span>' +
                    '<span class="day-label">T</span><span class="day-label">W</span>' +
                    '<span class="day-label">T</span><span class="day-label">F</span>' +
                    '<span class="day-label">S</span>';

        for (var i = 0; i < firstDay; i++) {
            html += '<span class="day"></span>';
        }

        for (var d = 1; d <= daysInMonth; d++) {
            var dateStr = year + '-' + String(month + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
            var hasCartoon = cartoonDates[dateStr];
            var isToday = dateStr === todayStr;
            var cls = 'day';
            if (hasCartoon) cls += ' has-cartoon';
            if (isToday) cls += ' today';
            html += '<span class="' + cls + '"' + (hasCartoon ? ' data-date="' + dateStr + '"' : '') + '>' + d + '</span>';
        }

        html += '</div></div>';
        section.innerHTML = html;

        document.getElementById('cal-prev').addEventListener('click', function () {
            if (hasPrev) {
                var prev = cartoonMonths[mi + 1];
                renderCalendarSection(prev.year, prev.month);
            }
        });

        document.getElementById('cal-next').addEventListener('click', function () {
            if (hasNext) {
                var next = cartoonMonths[mi - 1];
                renderCalendarSection(next.year, next.month);
            }
        });

        document.getElementById('calendar-jump').addEventListener('change', function () {
            var parts = this.value.split('-');
            renderCalendarSection(parseInt(parts[0]), parseInt(parts[1]));
        });

        section.querySelectorAll('.day.has-cartoon').forEach(function (day) {
            day.addEventListener('click', function () {
                var cartoon = cartoons.find(function (c) { return c.date === day.dataset.date; });
                if (cartoon) {
                    renderHero(cartoon);
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            });
        });
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
    }

    // ── Voting (Supabase + localStorage) ─────────────────

    function getVotes(id) {
        // Supabase counts take priority
        if (voteCounts[id]) return voteCounts[id];
        // Fallback to localStorage
        try {
            var data = JSON.parse(localStorage.getItem('ns_votes_' + id) || '{}');
            return { likes: data.likes || 0, dislikes: data.dislikes || 0 };
        } catch (e) { return { likes: 0, dislikes: 0 }; }
    }

    function getVoted(id) {
        try { return localStorage.getItem('ns_voted_' + id) || ''; }
        catch (e) { return ''; }
    }

    function vote(id, type) {
        // Update local counts immediately
        if (!voteCounts[id]) voteCounts[id] = { likes: 0, dislikes: 0 };
        if (type === 'like') voteCounts[id].likes++;
        else voteCounts[id].dislikes++;

        // Mark as voted in localStorage
        try {
            localStorage.setItem('ns_voted_' + id, type);
        } catch (e) { /* quota */ }

        // Write to Supabase
        if (supabaseClient) {
            supabaseClient.from('ns_votes').insert({
                cartoon_id: id,
                vote: type,
                visitor_id: getVisitorId(),
            }).then(function () {}).catch(function () {});
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
