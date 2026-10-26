// ===== Module: onboarding =====
// First-run walkthrough for new recl/\mr users.
// Shows on first open (localStorage 'reclmr_onboarded'), walks through
// Tailscale setup, phone PWA install, and app configuration.

var ONBOARDING_STEPS = 5; // 0=welcome, 1=features, 2=security, 3=phone, 4=done
var onboardingCurrentStep = 0;

function initOnboarding() {
  var overlay = document.getElementById('onboarding-overlay');
  if (!overlay) return;
  // Only show if not yet onboarded
  if (localStorage.getItem('reclmr_onboarded') === '1') {
    overlay.style.display = 'none';
    return;
  }
  // Wait for the sign-in: the walkthrough saves your name and the companion's name, which needs a signed-in session
  var loginScreen = document.getElementById('login-screen');
  if (loginScreen && loginScreen.style.display !== 'none' && loginScreen.style.display !== '') {
    overlay.style.display = 'none';
    setTimeout(initOnboarding, 500);
    return;
  }
  // Show the overlay
  overlay.style.display = 'flex';
  onboardingCurrentStep = 0;
  buildProgressDots();
  generateOnboardingQRs();
  showOnboardingStep(0);
  // Prefill the agent name with the current value (if already set).
  fetch('/api/profile').then(function(r) { return r.json(); }).then(function(d) {
    var u = document.getElementById('user-name-input');
    if (u && d.name && !u.value) u.value = d.name;
  }).catch(function() {});
  fetch('/api/voice/config').then(function(r) { return r.json(); }).then(function(d) {
    var input = document.getElementById('agent-name-input');
    if (input && d.config && d.config.agent_name) input.value = d.config.agent_name;
  }).catch(function() {});
}

function buildProgressDots() {
  var bar = document.getElementById('onboarding-progress');
  if (!bar) return;
  bar.innerHTML = '';
  for (var i = 0; i < ONBOARDING_STEPS; i++) {
    var dot = document.createElement('div');
    dot.className = 'onboarding-progress-dot';
    bar.appendChild(dot);
  }
}

function showOnboardingStep(step) {
  onboardingCurrentStep = step;
  // Show/hide step divs
  var steps = document.querySelectorAll('.onboarding-step');
  steps.forEach(function(s) { s.classList.remove('active'); });
  var target = document.querySelector('.onboarding-step[data-step="' + step + '"]');
  if (target) target.classList.add('active');

  // Update progress dots
  var dots = document.querySelectorAll('.onboarding-progress-dot');
  dots.forEach(function(d, i) {
    d.classList.remove('completed', 'active');
    if (i < step) d.classList.add('completed');
    else if (i === step) d.classList.add('active');
  });

  // Update nav buttons
  var nextBtn = document.getElementById('onboarding-next');
  var prevBtn = document.getElementById('onboarding-prev');
  var skipBtn = document.getElementById('onboarding-skip');

  // Next button text: 0="Get Started", 1-3="Next", 4="Finish"
  var labels = ['Get Started', 'Next', 'Next', 'Next', 'Finish'];
  if (nextBtn) nextBtn.textContent = labels[step] || 'Next';

  // Back button: show on step > 0
  if (prevBtn) prevBtn.style.display = step > 0 ? '' : 'none';

  // Skip button: hide on last step
  if (skipBtn) skipBtn.style.display = step < ONBOARDING_STEPS - 1 ? '' : 'none';
}

function onboardingNext() {
  if (onboardingCurrentStep >= ONBOARDING_STEPS - 1) {
    onboardingFinish();
    return;
  }
  showOnboardingStep(onboardingCurrentStep + 1);
}

function onboardingPrev() {
  if (onboardingCurrentStep > 0) {
    showOnboardingStep(onboardingCurrentStep - 1);
  }
}

function onboardingSkip() {
  finishOnboarding(true);
}

function onboardingFinish() {
  finishOnboarding(false);
}

function finishOnboarding(skipped) {
  // Persist your name (the prompt at the top of the home screen).
  var userInput = document.getElementById('user-name-input');
  var userName = userInput ? userInput.value.trim() : '';
  if (userName) saveUserName(userName);
  // Persist the chosen agent name (empty = keep default).
  var nameInput = document.getElementById('agent-name-input');
  var name = nameInput ? nameInput.value.trim() : '';
  if (name) {
    fetch('/api/voice/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_name: name })
    }).catch(function() {});
  }
  localStorage.setItem('reclmr_onboarded', '1');
  var overlay = document.getElementById('onboarding-overlay');
  if (overlay) {
    overlay.style.display = 'none';
  }
}

/**
 * Re-open the onboarding flow (called from Settings).
 * Clears the flag and re-initializes.
 */
function reopenOnboarding() {
  localStorage.removeItem('reclmr_onboarded');
  // If currently in settings, go home first
  if (typeof goHome === 'function') {
    goHome();
  }
  initOnboarding();
}

/**
 * Generate QR codes for the onboarding steps.
 * Rendered client-side (qrcode-generator from jsdelivr) so the tunnel URL
 * never leaves the browser.
 */
/** True for addresses a phone cannot reach: localhost and private home-network ranges. */
function isLocalOnlyHost(host) {
  return host === 'localhost' || host === '[::1]' || /\.local$/i.test(host) ||
    /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0$)/.test(host);
}

function generateOnboardingQRs() {
  var local = isLocalOnlyHost(window.location.hostname);
  // Show the URL hint
  var hint = document.getElementById('onboarding-url-hint');
  if (hint) {
    hint.textContent = local ? '' : window.location.origin;
  }

  loadVendor('qrcode').then(function () {                        // the QR library is only fetched when onboarding actually shows
    // Tailscale download QR
    var tailscaleContainer = document.getElementById('qr-tailscale');
    if (tailscaleContainer && !tailscaleContainer.hasChildNodes()) {
      var img1 = generateQR('https://tailscale.com/download', 300);
      if (img1) tailscaleContainer.appendChild(img1);
    }

    // Phone PWA QR: the address this page was opened on, which is the tunnel address when you came in through one.
    // On localhost that address is useless to a phone, so ask for the tunnel address instead.
    var phoneContainer = document.getElementById('qr-phone');
    if (!phoneContainer) return;
    if (!local) {
      if (!phoneContainer.hasChildNodes()) { var img2 = generateQR(window.location.origin, 300); if (img2) phoneContainer.appendChild(img2); }
      return;
    }
    var box = document.getElementById('qr-local'), input = document.getElementById('qr-url-input'), shown = document.getElementById('qr-local-origin');
    if (!box || !input) return;
    box.hidden = false; if (shown) shown.textContent = window.location.origin;
    input.addEventListener('input', function () {
      phoneContainer.textContent = '';
      var u; try { u = new URL(input.value.trim()); } catch (_) { return; }
      if ((u.protocol !== 'https:' && u.protocol !== 'http:') || isLocalOnlyHost(u.hostname)) return;
      var img = generateQR(u.origin, 300); if (img) phoneContainer.appendChild(img);
    });
  }).catch(function () {});
}

/** Client-side QR as a data-URL <img>; null if the lib failed to load. */
function generateQR(text, size) {
  if (typeof qrcode !== 'function') return null;
  var s = size || 200;
  var qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  var img = document.createElement('img');
  img.src = qr.createDataURL(Math.max(2, Math.floor(s / (qr.getModuleCount() + 8))), 4);
  img.width = s;
  img.height = s;
  img.style.imageRendering = 'pixelated';
  img.alt = 'QR: ' + text;
  return img;
}

// ─── Boot ───────────────────────────────────────────────
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initOnboarding);
} else {
  initOnboarding();
}
