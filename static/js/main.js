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
function setFavicon(theme) {
  const icon = theme === 'light' ? 'light mode_icon.ico' : 'dark mode_icon.ico';
  const link = document.getElementById('favicon');
  if (link) {
    link.href = `/static/images/${icon}`;
  }
}

function initTheme() {
  const savedStatus = localStorage.getItem('drizl-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', savedStatus);
  setFavicon(savedStatus);
}

function toggleTheme() {
  const curr = document.documentElement.getAttribute('data-theme');
  const next = curr === 'dark' ? 'light' : 'dark';
  
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('drizl-theme', next);
  setFavicon(next);
  
  showToast(`Switched to ${next} mode`, 'success');
}

// Initial Run
document.addEventListener('DOMContentLoaded', initTheme);
