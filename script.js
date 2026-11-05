(function () {
  var yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  var navToggle = document.getElementById('nav-toggle');
  var mainNav = document.getElementById('main-nav');
  if (navToggle && mainNav) {
    navToggle.addEventListener('click', function () {
      var open = mainNav.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    mainNav.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        mainNav.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // The Services menu: its button opens the list of service pages (on wide
  // screens hovering opens it too, see styles.css). Escape, a click elsewhere
  // or tabbing away closes it.
  document.querySelectorAll('.nav-dropdown').forEach(function (dropdown) {
    var button = dropdown.querySelector('.nav-dropdown-toggle');
    if (!button) return;
    function setOpen(open) {
      dropdown.classList.toggle('open', open);
      button.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    button.addEventListener('click', function () {
      setOpen(!dropdown.classList.contains('open'));
    });
    dropdown.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && dropdown.classList.contains('open')) {
        setOpen(false);
        button.focus();
      }
    });
    dropdown.addEventListener('focusout', function (e) {
      if (!dropdown.contains(e.relatedTarget)) setOpen(false);
    });
    document.addEventListener('click', function (e) {
      if (!dropdown.contains(e.target)) setOpen(false);
    });
  });

  // Videos carry their file in data-src, so nothing downloads until the
  // player is close to the screen; then it plays muted on a loop.
  var videos = document.querySelectorAll('video[data-src]');
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function loadVideo(video) {
    if (!video.getAttribute('data-src')) return;
    video.src = video.getAttribute('data-src');
    video.removeAttribute('data-src');
    video.muted = true;
    video.controls = true;
    if (reduceMotion) return;
    var playing = video.play();
    if (playing && playing.catch) playing.catch(function () {});
  }

  if (videos.length) {
    if ('IntersectionObserver' in window) {
      var watcher = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          watcher.unobserve(entry.target);
          loadVideo(entry.target);
        });
      }, { rootMargin: '300px 0px' });
      Array.prototype.forEach.call(videos, function (video) { watcher.observe(video); });
    } else {
      window.addEventListener('load', function () {
        Array.prototype.forEach.call(videos, loadVideo);
      });
    }
  }

  var form = document.getElementById('inquiry-form');
  var errorEl = document.getElementById('form-error');
  if (!form) return;

  // Service pages name their service on the form; preselect it.
  var pageService = form.getAttribute('data-service');
  function preselectService() {
    if (pageService && !form.service.value) form.service.value = pageService;
  }
  preselectService();

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    var name = form.name.value.trim();
    var phone = form.phone.value.trim();
    var service = form.service.value.trim();
    var address = form.address.value.trim();
    var message = form.message.value.trim();

    if (!name || !phone || !service || !message) {
      errorEl.hidden = false;
      errorEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    errorEl.hidden = true;

    var lines = [
      'New inquiry from the Sudbury Workforce website:',
      '',
      'Name: ' + name,
      'Phone: ' + phone,
      'Service: ' + service
    ];
    if (address) lines.push('Address/Area: ' + address);
    lines.push('', 'Details: ' + message);
    // Which page the lead came from (free lead-source tracking).
    lines.push('', 'Page: ' + (form.getAttribute('data-page') || document.title));

    // The number is business.whatsapp in site.json, put on the form by build.py.
    var text = encodeURIComponent(lines.join('\n'));
    var url = 'https://wa.me/' + form.getAttribute('data-whatsapp') + '?text=' + text;

    window.open(url, '_blank', 'noopener');
    form.reset();
    preselectService();
  });
})();
