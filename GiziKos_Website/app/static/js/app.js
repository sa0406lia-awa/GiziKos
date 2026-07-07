(() => {
  /* ---- Nav Toggle (mobile) ---- */
  const toggle = document.querySelector('.nav-toggle');
  const nav = document.querySelector('.main-nav');
  if (toggle && nav) {
    toggle.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      toggle.classList.toggle('active', open);
      toggle.setAttribute('aria-expanded', String(open));
    });
  }

  /* ---- User Dropdown ---- */
  const dropdown = document.getElementById('userDropdown');
  const dropdownBtn = document.getElementById('userDropdownBtn');
  if (dropdown && dropdownBtn) {
    dropdownBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      dropdown.classList.toggle('open');
      dropdownBtn.setAttribute('aria-expanded', String(dropdown.classList.contains('open')));
    });
    document.addEventListener('click', (e) => {
      if (!dropdown.contains(e.target)) {
        dropdown.classList.remove('open');
        dropdownBtn.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        dropdown.classList.remove('open');
        dropdownBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* ---- Header scroll shadow ---- */
  const header = document.getElementById('siteHeader');
  if (header) {
    let ticking = false;
    const onScroll = () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          header.classList.toggle('scrolled', window.scrollY > 8);
          ticking = false;
        });
        ticking = true;
      }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ---- Reveal animation ---- */
  const observer = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1 }) : null;
  document.querySelectorAll('.reveal').forEach((el) => observer ? observer.observe(el) : el.classList.add('visible'));

  /* ---- Password toggle ---- */
  document.querySelectorAll('.password-toggle').forEach((button) => {
    button.addEventListener('click', () => {
      const input = button.closest('.password-field')?.querySelector('input');
      if (!input) return;
      const visible = input.type === 'text';
      input.type = visible ? 'password' : 'text';
      button.textContent = visible ? 'Lihat' : 'Sembunyikan';
    });
  });

  /* ---- Shopping list checkbox ---- */
  document.querySelectorAll('.shopping-list input').forEach((input) => {
    input.addEventListener('change', () => {
      const text = input.closest('li')?.querySelector('span');
      if (text) {
        text.style.textDecoration = input.checked ? 'line-through' : '';
        text.style.opacity = input.checked ? '.5' : '1';
      }
    });
  });

  /* ---- Flash auto-hide ---- */
  const flash = document.querySelector('.flash');
  if (flash) setTimeout(() => flash.classList.add('hide'), 4500);
})();
