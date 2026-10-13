'use strict';
/* Shared by every page (overview, viewer, cohort builder): theme, the toad's hop and "boing". */

const savedTheme = (() => { try { return localStorage.getItem('cal.theme'); } catch { return null; } })();
if (savedTheme) document.documentElement.dataset.theme = savedTheme;

/* The new theme spreads out as a circle from the button (View Transitions); instant where unsupported. */
function toggleTheme(e) {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const apply = () => { root.dataset.theme = dark ? 'light' : 'dark'; (() => { try { localStorage.setItem('cal.theme', root.dataset.theme); } catch { /* unavailable */ } })(); };
  if (!document.startViewTransition || matchMedia('(prefers-reduced-motion: reduce)').matches) return apply();
  const r = e?.currentTarget?.getBoundingClientRect?.();
  const x = r ? r.left + r.width / 2 : innerWidth - 40, y = r ? r.top + r.height / 2 : 28;
  // Percentages of the page, not pixels: some webviews scale pixel clip-paths by the device pixel ratio.
  const W = innerWidth, H = innerHeight, at = `at ${(100 * x / W).toFixed(3)}% ${(100 * y / H).toFixed(3)}%`;
  const radius = 100 * Math.hypot(Math.max(x, W - x), Math.max(y, H - y)) / (Math.hypot(W, H) / Math.SQRT2);
  document.startViewTransition(apply).ready.then(() => root.animate(
    {clipPath: [`circle(0% ${at})`, `circle(${radius.toFixed(3)}% ${at})`]},
    {duration: 550, easing: 'cubic-bezier(.4, 0, .2, 1)', pseudoElement: '::view-transition-new(root)'}));
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('theme')?.addEventListener('click', toggleTheme);
  document.getElementById('toad')?.addEventListener('click', () => { hop(); boing(); });
});


function hop() {
  const b = document.body;
  b.classList.remove('hop'); void b.offsetWidth; b.classList.add('hop');
}

/* A synthesized "boing" for the toad: a pitch swoop wobbled by a fast, fading vibrato. */
let audio = null;
function boing() {
  try {
    audio = audio || new AudioContext();
    if (audio.state === 'suspended') audio.resume();
    const t = audio.currentTime;
    const pitch = 0.92 + Math.random() * 0.16;  // a little different every hop
    const osc = audio.createOscillator(), amp = audio.createGain();
    const wobble = audio.createOscillator(), depth = audio.createGain();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(150 * pitch, t);
    osc.frequency.exponentialRampToValueAtTime(560 * pitch, t + 0.09);
    osc.frequency.exponentialRampToValueAtTime(230 * pitch, t + 0.55);
    wobble.frequency.setValueAtTime(22, t);
    wobble.frequency.linearRampToValueAtTime(12, t + 0.55);
    depth.gain.setValueAtTime(90 * pitch, t);
    depth.gain.exponentialRampToValueAtTime(4, t + 0.55);
    amp.gain.setValueAtTime(0.0001, t);
    amp.gain.exponentialRampToValueAtTime(0.22, t + 0.015);
    amp.gain.exponentialRampToValueAtTime(0.0001, t + 0.6);
    wobble.connect(depth).connect(osc.frequency);
    osc.connect(amp).connect(audio.destination);
    for (const o of [osc, wobble]) { o.start(t); o.stop(t + 0.62); }
  } catch { /* no audio available; the hop still happens */ }
}
