(() => {
  const form = document.getElementById('consultationForm');
  if (!form) return;

  const steps = [...form.querySelectorAll('.wizard-step')];
  const navItems = [...document.querySelectorAll('#wizardNav li')];
  const nextBtn = document.getElementById('nextBtn');
  const prevBtn = document.getElementById('prevBtn');
  const submitBtn = document.getElementById('submitBtn');
  const progress = document.querySelector('.progress-ring');
  const progressPercent = document.getElementById('progressPercent');
  const progressTitle = document.getElementById('progressTitle');
  let current = 0;

  const showStep = (index) => {
    current = Math.max(0, Math.min(index, steps.length - 1));
    steps.forEach((step, i) => step.classList.toggle('active', i === current));
    navItems.forEach((item, i) => {
      item.classList.toggle('active', i === current);
      item.classList.toggle('completed', i < current);
    });
    prevBtn.hidden = current === 0;
    nextBtn.hidden = current === steps.length - 1;
    submitBtn.hidden = current !== steps.length - 1;
    const pct = Math.round(((current + 1) / steps.length) * 100);
    progressPercent.textContent = `${pct}%`;
    progressTitle.textContent = steps[current].dataset.title || '';
    progress.style.background = `conic-gradient(var(--primary) ${pct}%, #dce9e5 0)`;
    window.scrollTo({ top: Math.max(0, form.getBoundingClientRect().top + window.scrollY - 110), behavior: 'smooth' });
  };

  const validateStep = () => {
    const fields = [...steps[current].querySelectorAll('input, select, textarea')].filter((field) => !field.disabled && !field.hidden);
    for (const field of fields) {
      if (!field.checkValidity()) {
        field.reportValidity();
        field.focus();
        return false;
      }
    }
    if (current === 1 && !form.querySelector('input[name="tools"]:checked')) {
      alert('Pilih minimal satu alat masak atau pilih “Tidak punya alat masak”.');
      return false;
    }
    return true;
  };

  nextBtn.addEventListener('click', () => { if (validateStep()) showStep(current + 1); });
  prevBtn.addEventListener('click', () => showStep(current - 1));
  navItems.forEach((item, index) => item.querySelector('button').addEventListener('click', () => {
    if (index <= current || validateStep()) showStep(index);
  }));

  const formatRupiah = (value) => `Rp${Number(value).toLocaleString('id-ID')}`;
  const budgetRange = document.getElementById('budgetRange');
  const budgetHidden = document.getElementById('dailyBudget');
  const budgetDisplay = document.getElementById('budgetDisplay');
  const budgetTier = document.getElementById('budgetTier');
  const updateBudget = (value) => {
    const amount = Number(value);
    budgetRange.value = String(amount);
    budgetHidden.value = String(amount);
    budgetDisplay.textContent = formatRupiah(amount);
    budgetTier.textContent = amount <= 25000 ? 'Sangat hemat' : amount <= 40000 ? 'Cukup hemat' : amount <= 60000 ? 'Fleksibel' : 'Lebih leluasa';
    document.querySelectorAll('[data-budget]').forEach((button) => button.classList.toggle('active', Number(button.dataset.budget) === amount));
  };
  budgetRange?.addEventListener('input', () => updateBudget(budgetRange.value));
  document.querySelectorAll('[data-budget]').forEach((button) => button.addEventListener('click', () => updateBudget(button.dataset.budget)));
  updateBudget(budgetHidden.value);

  const noTools = document.getElementById('noTools');
  const normalTools = [...form.querySelectorAll('input[name="tools"]')].filter((input) => input.value !== 'none');
  const noToolsWarning = document.getElementById('noToolsWarning');
  noTools?.addEventListener('change', () => {
    if (noTools.checked) normalTools.forEach((input) => { input.checked = false; });
    noToolsWarning.hidden = !noTools.checked;
  });
  normalTools.forEach((input) => input.addEventListener('change', () => {
    if (input.checked && noTools) noTools.checked = false;
    if (noToolsWarning) noToolsWarning.hidden = true;
  }));

  const filterList = (inputId, selector) => {
    const search = document.getElementById(inputId);
    if (!search) return;
    search.addEventListener('input', () => {
      const query = search.value.toLowerCase().trim();
      document.querySelectorAll(selector).forEach((chip) => {
        chip.classList.toggle('is-hidden', Boolean(query) && !chip.dataset.name.includes(query));
      });
    });
  };
  filterList('dislikedSearch', '.dislike-chip');
  filterList('ingredientSearch', '.ingredient-chip:not([hidden])');

  const dislikedCount = document.getElementById('dislikedCount');
  const stockCount = document.getElementById('stockCount');
  const restrictionNotice = document.getElementById('restrictionNotice');
  const allergyInputs = [...form.querySelectorAll('input[name="allergies"]')];
  const dislikedInputs = [...form.querySelectorAll('input[name="disliked_ingredients"]')];
  const vegetarian = document.getElementById('vegetarianToggle');
  const stockChips = [...form.querySelectorAll('.ingredient-chip')];

  const updateCounts = () => {
    if (dislikedCount) dislikedCount.textContent = `${dislikedInputs.filter((item) => item.checked).length} dipilih`;
    if (stockCount) stockCount.textContent = String(stockChips.filter((chip) => chip.querySelector('input').checked).length);
  };

  const syncRestrictions = () => {
    const allergies = new Set(allergyInputs.filter((item) => item.checked).map((item) => item.value));
    const disliked = new Set(dislikedInputs.filter((item) => item.checked).map((item) => item.value));
    let blockedCount = 0;
    stockChips.forEach((chip) => {
      const input = chip.querySelector('input');
      const chipAllergens = new Set((chip.dataset.allergens || '').split(';').filter(Boolean));
      const blockedAllergy = [...chipAllergens].some((item) => allergies.has(item));
      const blockedDisliked = disliked.has(chip.dataset.slug);
      const blockedVegetarian = Boolean(vegetarian?.checked) && chip.dataset.vegetarian !== 'true';
      const blocked = blockedAllergy || blockedDisliked || blockedVegetarian;
      chip.hidden = blocked;
      input.disabled = blocked;
      if (blocked) {
        input.checked = false;
        blockedCount += 1;
      }
    });
    if (restrictionNotice) restrictionNotice.hidden = blockedCount === 0;
    document.querySelectorAll('.ingredient-group').forEach((group) => {
      const visible = [...group.querySelectorAll('.ingredient-chip')].some((chip) => !chip.hidden);
      group.hidden = !visible;
    });
    updateCounts();
  };

  [...allergyInputs, ...dislikedInputs, vegetarian].filter(Boolean).forEach((input) => input.addEventListener('change', syncRestrictions));
  stockChips.forEach((chip) => chip.querySelector('input').addEventListener('change', updateCounts));

  document.querySelectorAll('.select-category').forEach((button) => {
    button.addEventListener('click', () => {
      const group = button.closest('.ingredient-group');
      const boxes = [...group.querySelectorAll('.ingredient-chip:not([hidden]) input[type="checkbox"]')].filter((box) => !box.disabled && !box.closest('.ingredient-chip').classList.contains('is-hidden'));
      const shouldCheck = boxes.some((box) => !box.checked);
      boxes.forEach((box) => { box.checked = shouldCheck; });
      button.textContent = shouldCheck ? 'Batalkan pilihan' : 'Pilih semua yang aman';
      updateCounts();
    });
  });

  form.addEventListener('submit', (event) => {
    if (!validateStep()) {
      event.preventDefault();
      return;
    }
    syncRestrictions();
    submitBtn.classList.add('loading');
    submitBtn.disabled = true;
  });

  syncRestrictions();
  showStep(0);
})();
