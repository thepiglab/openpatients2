'use strict';
/* The toad, shared by the overview and the viewer: a hop and a synthesized "boing". */

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
