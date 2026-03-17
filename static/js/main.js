// Toast Utility
function showToast(msg, type = 'success') {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `
    <span>${type === 'success' ? '✓' : '⚠️'}</span>
    <span>${msg}</span>
  `;
  document.body.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(12px)';
    el.style.transition = 'all 0.2s ease';
    setTimeout(() => el.remove(), 200);
  }, 3000);
}

// Landing page nav scroll effect
window.addEventListener('scroll', () => {
  const nav = document.querySelector('.l-nav');
  if (nav) {
    if (window.scrollY > 50) nav.classList.add('scrolled');
    else nav.classList.remove('scrolled');
  }
});

// Theme Management
function initTheme() {
  const saved = localStorage.getItem('drizl-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('drizl-theme', next);
  showToast(`${next.charAt(0).toUpperCase() + next.slice(1)} mode enabled`, 'success');
}

// Ensure theme is initialized on script load
initTheme();
