(function () {
  var WHATSAPP_NUMBER = '17055629047'; // country code + number, no symbols

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

  var form = document.getElementById('inquiry-form');
  var errorEl = document.getElementById('form-error');
  if (!form) return;

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

    var text = encodeURIComponent(lines.join('\n'));
    var url = 'https://wa.me/' + WHATSAPP_NUMBER + '?text=' + text;

    window.open(url, '_blank', 'noopener');
    form.reset();
  });
})();
