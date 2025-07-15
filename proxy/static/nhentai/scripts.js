// Shared JavaScript for Gallery Templates

function toggleDarkMode() {
    const isDark = document.documentElement.style.getPropertyValue('--bg-color') === '#1a1a1a';
    const toggle = document.querySelector('.dark-mode-toggle');

    if (isDark) {
        // Switch to light mode
        setLightMode();
        toggle.textContent = '🌙';
        localStorage.setItem('dark-mode', 'false');
    } else {
        // Switch to dark mode
        setDarkMode();
        toggle.textContent = '☀️';
        localStorage.setItem('dark-mode', 'true');
    }
}

function setLightMode() {
    document.documentElement.style.setProperty('--bg-color', '#f5f5f5');
    document.documentElement.style.setProperty('--card-bg', 'white');
    document.documentElement.style.setProperty('--text-primary', '#333');
    document.documentElement.style.setProperty('--text-secondary', '#666');
    document.documentElement.style.setProperty('--border-color', '#ddd');
    document.documentElement.style.setProperty('--shadow', '0 2px 4px rgba(0,0,0,0.1)');
    document.documentElement.style.setProperty('--header-bg', '#667eea');
    document.documentElement.style.setProperty('--header-text', 'white');
    document.documentElement.style.setProperty('--info-bg', '#f8f9fa');
    document.documentElement.style.setProperty('--tag-bg', '#e9ecef');
    document.documentElement.style.setProperty('--tag-text', '#495057');
    document.documentElement.style.setProperty('--tag-border', '#dee2e6');
    document.documentElement.style.setProperty('--button-primary', '#667eea');
    document.documentElement.style.setProperty('--button-primary-hover', '#5a6fd8');
    document.documentElement.style.setProperty('--button-success', '#28a745');
    document.documentElement.style.setProperty('--button-success-hover', '#218838');
    document.documentElement.style.setProperty('--nav-bg', '#f8f9fa');
    document.documentElement.style.setProperty('--nav-border', '#e9ecef');
}

function setDarkMode() {
    document.documentElement.style.setProperty('--bg-color', '#1a1a1a');
    document.documentElement.style.setProperty('--card-bg', '#2d2d2d');
    document.documentElement.style.setProperty('--text-primary', '#e0e0e0');
    document.documentElement.style.setProperty('--text-secondary', '#b0b0b0');
    document.documentElement.style.setProperty('--border-color', '#444');
    document.documentElement.style.setProperty('--shadow', '0 2px 4px rgba(0,0,0,0.3)');
    document.documentElement.style.setProperty('--header-bg', '#4a5cc5');
    document.documentElement.style.setProperty('--header-text', '#f0f0f0');
    document.documentElement.style.setProperty('--info-bg', '#3a3a3a');
    document.documentElement.style.setProperty('--tag-bg', '#4a4a4a');
    document.documentElement.style.setProperty('--tag-text', '#d0d0d0');
    document.documentElement.style.setProperty('--tag-border', '#555');
    document.documentElement.style.setProperty('--button-primary', '#5a6fd8');
    document.documentElement.style.setProperty('--button-primary-hover', '#4a5cc5');
    document.documentElement.style.setProperty('--button-success', '#20a745');
    document.documentElement.style.setProperty('--button-success-hover', '#1e7e34');
    document.documentElement.style.setProperty('--nav-bg', '#3a3a3a');
    document.documentElement.style.setProperty('--nav-border', '#555');
}

// Initialize dark mode based on saved preference or system preference
function initDarkMode() {
    const savedMode = localStorage.getItem('dark-mode');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const toggle = document.querySelector('.dark-mode-toggle');

    if (savedMode === 'true' || (savedMode === null && prefersDark)) {
        setDarkMode();
        if (toggle) toggle.textContent = '☀️';
    } else {
        setLightMode();
        if (toggle) toggle.textContent = '🌙';
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', initDarkMode);
