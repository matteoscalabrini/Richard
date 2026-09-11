"""The single-page config UI, served as one self-contained HTML string.

Kept as a Python constant so the web server has zero static-file dependencies — no
template engine, no on-disk assets, no build step. The page is plain HTML + a little
vanilla JS that talks to the JSON API under /api/*.

The visual language is a black terminal theme in IBM Plex Mono, 2px white "pixel"
borders with zero radius, collapsible `system.*.*` windows, square glowing status
dots, and per-section Save buttons with a status line — a retro console look that
matches Richard's voice-satellite hardware aesthetic.
"""

SPA_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Richard — Home</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
<meta name="theme-color" content="#000000">
<link rel="icon" href="/icon">
<link rel="apple-touch-icon" href="/icon">
<style>
  :root {
    --bg-color: #000000;
    --surface-color: #000000;
    --surface-light: #222222;
    --primary-color: #ffffff;
    --text-primary: #ffffff;
    --text-secondary: #aaaaaa;
    --error-color: #ff0000;
    --success-color: #00ff00;
    --button-hover: #333333;
    --terminal-prompt: #ffffff;
    --pixel-border: 2px solid #ffffff;
    --header-height: 80px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: "IBM Plex Mono", monospace;
    background-color: var(--bg-color); color: var(--text-primary);
    line-height: 1.5; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  }
  .container {
    display: flex; flex-direction: column; min-height: 100vh;
    width: 100%; max-width: 540px; margin: 0 auto; padding: 1rem; position: relative;
  }
  header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 1rem 0; border-bottom: 1px solid rgba(255,255,255,0.1);
    position: sticky; top: 0; background-color: var(--bg-color);
    z-index: 1010; height: var(--header-height);
  }
  .terminal-header { display: flex; align-items: center; height: 100%; min-width: 0; }
  .terminal-prompt { color: var(--terminal-prompt); margin-right: 0.5rem; font-weight: bold; }
  .terminal-prompt.smaller { font-size: 0.8rem; }
  h1 { font-size: 1.6rem; font-weight: 700; color: var(--primary-color); letter-spacing: 0.04em; }
  .right-header { display: flex; align-items: center; gap: 1rem; height: 100%; }
  .status-indicator { display: flex; align-items: center; gap: 0.5rem; font-size: 0.8rem; color: var(--text-secondary); }
  .status-dot {
    width: 10px; height: 10px; background-color: var(--success-color);
    image-rendering: pixelated; box-shadow: 0 0 6px var(--success-color); border-radius: 0;
  }
  .status-indicator.disconnected .status-dot { background-color: var(--error-color); box-shadow: 0 0 4px var(--error-color); }
  .status-indicator.disconnected .status-text { color: var(--error-color); }

  main { flex: 1; display: flex; flex-direction: column; gap: 0; padding: 1.25rem 0; }

  .terminal-section {
    background-color: var(--surface-color); padding: 1rem; margin-bottom: 1.25rem;
    border: var(--pixel-border); image-rendering: pixelated;
  }
  .terminal-header-line {
    display: flex; align-items: center; cursor: pointer;
    border-bottom: var(--pixel-border); padding-bottom: 0.5rem; margin-bottom: 0.85rem;
  }
  .terminal-command { color: var(--text-primary); }
  .window-title { flex-grow: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .window-control { margin-left: 1rem; color: var(--text-primary); font-weight: bold; user-select: none; }
  .window-control.is-open { color: var(--success-color); }
  .terminal-header-line:hover .window-control { color: var(--success-color); }
  .window-content { overflow: hidden; transition: max-height 0.3s ease; max-height: 4000px; }
  .window-content.collapsed { max-height: 0; }

  .terminal-output { margin: 0.25rem 0 0.75rem; }
  .terminal-line { display: flex; align-items: center; margin-bottom: 0.35rem; }
  .terminal-line.indent { margin-left: 1.5rem; }
  .terminal-output-text { color: #dddddd; }
  .terminal-output-text b { color: var(--text-primary); font-weight: 600; }
  .smaller { font-size: 0.8rem; }
  .lede { color: var(--text-secondary); font-size: 0.8rem; margin: 0 0 0.85rem; }

  .setting-group { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 0.85rem; }
  .setting-label { font-size: 0.875rem; font-weight: 500; color: var(--text-secondary); display: flex; align-items: center; gap: 0.5rem; }
  .setting-label.spread { justify-content: space-between; }
  /* Help that belongs to one input: hangs off its left edge, directly under it. */
  .hint {
    display: block; font-size: 0.72rem; line-height: 1.4; color: var(--text-secondary);
    margin: 0; padding: 0.1rem 0 0 0.55rem; border-left: 2px solid #3a3a3a;
  }
  .window-content > .hint { margin: -0.45rem 0 0.85rem; }
  .setting-input {
    background-color: var(--surface-light); color: var(--text-primary); border: var(--pixel-border);
    padding: 0.5rem; font-family: "IBM Plex Mono", monospace; font-size: 0.875rem; width: 100%;
  }
  select.setting-input { appearance: none; -webkit-appearance: none; cursor: pointer; }
  .setting-input:focus { outline: none; border-color: var(--primary-color); }
  .setting-input.invalid { border-color: var(--error-color); color: var(--error-color); }
  textarea.setting-input { resize: vertical; min-height: 96px; line-height: 1.45; white-space: pre-wrap; }

  .field-row { display: flex; gap: 0.75rem; align-items: flex-start; }
  .field-row .setting-group { flex: 1; }

  .button-row { margin-top: 0.5rem; display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
  .setting-button {
    background-color: var(--surface-light); color: var(--text-primary); border: var(--pixel-border);
    padding: 0.55rem 0.9rem; font-size: 0.8rem; font-weight: 500; cursor: pointer;
    font-family: "IBM Plex Mono", monospace;
    transition: background-color 0.2s ease, transform 0.1s ease, border-color 0.2s ease;
  }
  .setting-button:hover { background-color: var(--button-hover); border-color: var(--primary-color); }
  .setting-button:active { transform: scale(0.96); }
  .setting-button.small { padding: 0.3rem 0.55rem; font-size: 0.7rem; }

  .status-line { font-size: 0.78rem; min-height: 1.1rem; color: var(--text-secondary); }
  .status-line.ok { color: var(--success-color); }
  .status-line.err { color: var(--error-color); }

  /* Chat */
  .chat-log {
    max-height: 340px; overflow-y: auto; border: var(--pixel-border); background: var(--surface-light);
    padding: 0.6rem; margin: 0.25rem 0 0.6rem; display: flex; flex-direction: column; gap: 0.55rem;
  }
  .chat-msg { display: flex; gap: 0.5rem; align-items: flex-start; }
  .chat-msg .who { flex-shrink: 0; color: var(--text-secondary); font-weight: 600; }
  .chat-msg.user .who { color: var(--text-primary); }
  .chat-msg .body { white-space: pre-wrap; word-break: break-word; }
  .chat-avatar { width: 18px; height: 18px; flex-shrink: 0; margin-top: 2px; object-fit: contain; }
  .chat-empty { color: var(--text-secondary); font-style: italic; }
  .chat-thumb { display: block; max-width: 160px; max-height: 120px; border: var(--pixel-border); margin-top: 0.3rem; }
  .chat-thumb[hidden], .status-indicator[hidden] { display: none; }  /* class display rules would beat [hidden] */
  .attach-chip {
    display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.35rem; padding: 0.3rem 0.5rem;
    border: var(--pixel-border); background: var(--surface-light); font-size: 0.8rem;
  }
  .attach-chip[hidden] { display: none; }  /* the class's display:flex would otherwise beat [hidden] */
  .attach-chip img { width: 36px; height: 36px; object-fit: cover; border: var(--pixel-border); }
  .attach-chip span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .attach-chip button { background: none; border: 0; color: var(--text-primary); cursor: pointer; font-size: 1rem; }
  .streaming::after { content: "▌"; animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .setting-button.recording { background: var(--error-color); color: #000; border-color: var(--error-color); }

  /* Dials (personality) — pixel slider language */
  .dial-value { color: var(--primary-color); font-weight: 700; letter-spacing: 0.05em; font-variant-numeric: tabular-nums; }
  input[type="range"].dial-slider {
    -webkit-appearance: none; appearance: none; width: 100%; height: 10px;
    background: var(--surface-light); border: var(--pixel-border); outline: none; cursor: pointer;
  }
  input[type="range"].dial-slider::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none; width: 22px; height: 22px;
    background: var(--text-primary); border: var(--pixel-border); cursor: pointer;
  }
  input[type="range"].dial-slider::-moz-range-thumb {
    width: 22px; height: 22px; background: var(--text-primary); border: var(--pixel-border); cursor: pointer;
  }

  /* Custom checkbox (pixel style) */
  .checkbox-btn { display: block; position: relative; padding-left: 30px; cursor: pointer; user-select: none; min-height: 18px; }
  .checkbox-btn input { position: absolute; opacity: 0; cursor: pointer; top: 0; left: 0; height: 18px; width: 18px; z-index: 2; }
  .checkbox-btn label { cursor: pointer; font-size: 0.875rem; color: var(--text-secondary); }
  .checkmark { position: absolute; top: 0; left: 0; height: 18px; width: 18px; border: 2px solid #fff; transition: 0.2s linear; z-index: 1; pointer-events: none; }
  .checkmark:after {
    content: ""; position: absolute; visibility: hidden; opacity: 0; left: 50%; top: 40%;
    width: 7px; height: 10px; border: 2px solid var(--success-color);
    filter: drop-shadow(0 0 5px var(--success-color)); border-width: 0 2px 2px 0;
    transition: 0.2s linear; transform: translate(-50%, -50%) rotate(-90deg) scale(0.2);
  }
  .checkbox-btn input:checked ~ .checkmark { transform: rotate(45deg); border: none; }
  .checkbox-btn input:checked ~ .checkmark:after { visibility: visible; opacity: 1; transform: translate(-50%, -50%) rotate(0deg) scale(1); }
  .checkbox-btn input:checked ~ label { color: var(--success-color); }

  /* Status readout (core panel) */
  .status-display { background-color: var(--surface-light); border: var(--pixel-border); padding: 0.75rem; margin: 0.25rem 0 0; }
  .status-item { display: flex; justify-content: space-between; gap: 0.75rem; margin-bottom: 0.5rem; }
  .status-item:last-child { margin-bottom: 0; }
  .status-label { color: var(--text-secondary); font-size: 0.85rem; }
  .status-value { color: var(--text-primary); font-weight: 500; font-size: 0.85rem; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Lists (entities / memory) */
  .entry-list { margin-top: 0.5rem; border: var(--pixel-border); background-color: var(--surface-light); padding: 0.5rem; }
  .entry {
    display: flex; justify-content: space-between; align-items: center; gap: 0.75rem;
    padding: 0.5rem; margin-bottom: 0.5rem; background-color: var(--surface-color); border: 1px solid var(--surface-light);
  }
  .entry:last-child { margin-bottom: 0; }
  .entry-main { flex-grow: 1; min-width: 0; }
  .entry-name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
  .entry-sub { font-size: 0.75rem; color: var(--text-secondary); }
  .entry-sub.online { color: var(--success-color); }
  .entry-sub.offline { color: var(--error-color); }
  .empty-message { color: var(--text-secondary); font-size: 0.8rem; text-align: center; padding: 1rem 0; }
  .remote-only-hidden { display: none !important; }
  .row-add { display: flex; gap: 0.5rem; margin-top: 0.25rem; }
  .row-add .setting-input { flex: 1; }

  /* Richard's conversation-first shell */
  body.menu-open { overflow: hidden; }
  .app-shell { min-height: 100vh; min-height: 100dvh; background: var(--bg-color); }
  .app-header {
    position: sticky; top: 0; z-index: 1010; height: var(--header-height);
    display: flex; align-items: center; justify-content: space-between;
    width: 100%; max-width: 1180px; margin: 0 auto; padding: 0 1rem;
    background: var(--bg-color); border-bottom: 1px solid #222222;
  }
  .app-header .terminal-header { gap: 0.5rem; }
  .app-header .terminal-prompt { margin: 0; }
  .menu-icon {
    width: 46px; height: 42px; display: grid; align-content: center; gap: 6px;
    padding: 8px; border: 0; background: var(--bg-color); color: var(--text-primary);
    cursor: pointer;
  }
  .menu-icon span {
    display: block; width: 100%; height: 3px; background: currentColor;
    transform-origin: center; transition: transform 0.2s ease, opacity 0.2s ease;
  }
  .menu-icon:focus-visible { outline: var(--pixel-border); outline-offset: 2px; }
  .menu-icon[aria-expanded="true"] { color: var(--error-color); }
  /* Bars are 3px tall with a 6px gap: one pitch is 9px. Rotating about the centre after
     moving the outer bars onto the middle one gives an X whose arms cross at their midpoints. */
  .menu-icon[aria-expanded="true"] span:nth-child(1) { transform: translateY(9px) rotate(45deg); }
  .menu-icon[aria-expanded="true"] span:nth-child(2) { opacity: 0; transform: scaleX(0); }
  .menu-icon[aria-expanded="true"] span:nth-child(3) { transform: translateY(-9px) rotate(-45deg); }
  .richard-home {
    min-height: calc(100vh - var(--header-height)); min-height: calc(100dvh - var(--header-height));
    display: grid; place-items: center; position: relative; overflow: hidden;
    padding: 2rem 1rem 9rem;
  }
  .entity-wrap { width: min(54vw, 430px); aspect-ratio: 1; position: relative; }
  .entity-wrap img {
    width: 100%; height: 100%; object-fit: cover; filter: contrast(1.18);
    animation: entity-ready 8s ease-in-out infinite;
  }
  .entity-state {
    position: absolute; inset: 0; display: grid; place-items: center;
    color: #777777; font-size: 0.7rem; letter-spacing: 0.18em; text-transform: uppercase;
  }
  .latest-reply {
    position: absolute; left: 50%; bottom: 7.25rem; transform: translateX(-50%);
    width: min(640px, calc(100% - 2rem)); color: #dddddd; text-align: center;
    font-size: 0.9rem; line-height: 1.55;
  }
  .latest-reply::before {
    content: "> richard"; display: block; margin-bottom: 0.35rem;
    color: var(--text-secondary); font-size: 0.7rem;
  }
  .composer-wrap {
    position: absolute; left: 50%; bottom: max(1.25rem, env(safe-area-inset-bottom));
    transform: translateX(-50%); width: min(680px, calc(100% - 2rem));
  }
  .composer { display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto auto; }
  .composer .setting-input { min-width: 0; border-right: 0; }
  .composer .setting-button { min-width: 58px; }
  .composer .composer-mic { border-left: 0; }
  .composer-wrap .status-line { padding-top: 0.35rem; text-align: center; }
  .drawer-backdrop {
    position: fixed; inset: var(--header-height) 0 0; z-index: 1003;
    border: 0; background: rgba(0, 0, 0, 0.68);
  }
  .richard-drawer {
    position: fixed; top: var(--header-height); right: 0; bottom: 0; z-index: 1005;
    width: min(520px, 48vw); padding: 1.25rem; overflow-y: auto;
    background: var(--bg-color); border-left: var(--pixel-border);
    transform: translateX(100%); transition: transform 0.24s ease;
  }
  .richard-drawer.is-open { transform: translateX(0); }
  .drawer-page[hidden] { display: none; }
  .drawer-title {
    display: flex; justify-content: space-between; align-items: baseline; gap: 1rem;
    padding-bottom: 0.7rem; margin-bottom: 1rem; border-bottom: var(--pixel-border);
    outline: none;
  }
  .drawer-title b { font-size: 0.9rem; font-weight: 600; letter-spacing: 0.05em; }
  .drawer-title span, .drawer-group-label { color: var(--text-secondary); font-size: 0.7rem; }
  .drawer-copy { margin: -0.35rem 0 1rem; color: var(--text-secondary); font-size: 0.78rem; }
  .drawer-nav { display: grid; gap: 0.45rem; }
  .drawer-nav button {
    min-height: 44px; display: flex; justify-content: space-between; align-items: center;
    gap: 1rem; padding: 0.65rem 0.75rem; border: 1px solid #444444;
    background: var(--bg-color); color: var(--text-primary); font: inherit;
    font-size: 0.82rem; text-align: left; cursor: pointer;
  }
  .drawer-nav button span { color: var(--text-secondary); font-size: 0.7rem; white-space: nowrap; }
  .drawer-nav button:hover, .drawer-nav button:focus-visible { border-color: var(--text-primary); outline: none; }
  .drawer-group-label { margin: 0.85rem 0 0.15rem; letter-spacing: 0.12em; }
  .drawer-back {
    margin-bottom: 1rem; border: 0; background: var(--bg-color); color: var(--text-secondary);
    font: inherit; font-size: 0.78rem; cursor: pointer;
  }
  .drawer-back:hover, .drawer-back:focus-visible { color: var(--text-primary); outline: none; }
  .drawer-page-body .terminal-section { padding: 0; margin: 0; border: 0; }
  .drawer-page-body .terminal-section + .terminal-section {
    margin-top: 1.4rem; padding-top: 1.15rem; border-top: 1px solid #2e2e2e;
  }
  /* Inside the drawer a section keeps its title as a small divider label, not a window bar. */
  .drawer-page-body .terminal-header-line {
    display: flex; pointer-events: none; border-bottom: 0; padding: 0; margin: 0 0 0.55rem;
    font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-secondary);
  }
  .drawer-page-body .terminal-header-line .terminal-command { color: var(--text-secondary); }
  .drawer-page-body .window-control { display: none; }
  .drawer-page-body .terminal-section:only-child .terminal-header-line { display: none; }
  .drawer-page-body .window-content { max-height: none; overflow: visible; }
  .drawer-page-body .window-content.collapsed { max-height: none; }
  .drawer-page-body .lede:first-child { margin-top: 0; }
  @keyframes entity-ready {
    0%, 100% { opacity: 0.82; transform: scale(0.985); }
    50% { opacity: 1; transform: scale(1.015); }
  }
  .entity-wrap[data-state="listening"] img { animation: entity-listening 1.1s ease-in-out infinite; }
  .entity-wrap[data-state="thinking"] img { animation: entity-thinking 0.8s ease-in-out infinite; }
  .entity-wrap[data-state="speaking"] img { animation: entity-speaking 0.55s ease-in-out infinite; }
  .entity-wrap[data-state="offline"] img { animation: none; opacity: 0.35; }
  .entity-wrap[data-state="offline"] .entity-state, .latest-reply.err { color: var(--error-color); }
  @keyframes entity-listening {
    0%, 100% { opacity: 0.82; transform: scale(0.99); }
    50% { opacity: 1; transform: scale(1.04); }
  }
  @keyframes entity-thinking {
    0%, 100% { opacity: 1; transform: scale(1.01); }
    50% { opacity: 0.62; transform: scale(0.98); }
  }
  @keyframes entity-speaking {
    0%, 100% { opacity: 0.84; transform: scale(0.99); }
    50% { opacity: 1; transform: scale(1.025); }
  }

  footer { border-top: 1px solid rgba(255,255,255,0.1); padding: 0.75rem 0; margin-top: auto; }
  .info-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 0.75rem; font-size: 0.8rem; color: var(--text-secondary); }
  .info-bar b { color: var(--text-primary); font-weight: 500; }

  /* Desktop: widen and flow the section windows into two columns */
  @media (min-width: 900px) {
    .container { max-width: 1040px; }
    .legacy-panels { display: block; column-count: 2; column-gap: 1.25rem; padding-top: 1.25rem; }
    .terminal-section { break-inside: avoid; }
  }
  @media (max-width: 480px) {
    .app-header { padding-inline: 0.75rem; }
    h1 { font-size: 1.3rem; }
    .field-row { flex-direction: column; gap: 0; }
  }
  @media (max-width: 700px) {
    .richard-drawer { width: 100%; border-left: 0; }
    .entity-wrap { width: min(82vw, 360px); }
    .latest-reply, .composer-wrap { width: calc(100% - 1.5rem); }
    .composer { grid-template-columns: minmax(0, 1fr) auto auto auto; }
    .composer-send {
      position: absolute; width: 1px; height: 1px; overflow: hidden;
      clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap;
    }
    .composer .composer-mic { border-left: var(--pixel-border); }
  }
  @media (max-width: 360px) {
    .app-header { padding-inline: 0.75rem; }
    .app-header .right-header { gap: 0.5rem; }
    .status-indicator { font-size: 0.7rem; }
    .latest-reply, .composer-wrap { width: calc(100% - 1.5rem); }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { scroll-behavior: auto !important; }
    .richard-drawer { transition: none; }
    .entity-wrap img { animation: none !important; }
  }
</style>
</head>
<body>
<div class="app-shell">
  <header class="app-header">
    <div class="terminal-header">
      <span class="terminal-prompt">&gt; </span>
      <h1>RICHARD</h1>
    </div>
    <div class="right-header">
      <span class="status-indicator" id="perception-indicator" title="This device's camera is streaming to Richard" hidden>◉ camera</span>
      <div class="status-indicator" id="connection-status">
        <span class="status-dot"></span>
        <span class="status-text" id="conn-text">online</span>
      </div>
      <button class="menu-icon" id="menu-toggle" type="button" aria-label="Open menu"
              aria-controls="richard-drawer" aria-expanded="false">
        <span></span><span></span><span></span>
      </button>
    </div>
  </header>

  <main id="richard-home" class="richard-home">
    <div class="entity-wrap" id="richard-entity" data-state="ready">
      <img src="/icon" alt="Richard, represented by a white particle ring">
      <span class="entity-state" id="entity-state" aria-live="polite">ready</span>
    </div>
    <p class="latest-reply" id="latest-reply" aria-live="polite">Ready when you are.</p>
    <div class="composer-wrap">
      <div class="attach-chip" id="chat-attach-chip" hidden>
        <img id="chat-attach-thumb" alt="">
        <span id="chat-attach-name"></span>
        <button type="button" id="chat-attach-clear" title="Remove the picture">×</button>
      </div>
      <div class="composer">
        <input id="chat-input" class="setting-input" placeholder="Ask Richard…" autocomplete="off">
        <button class="setting-button composer-send" id="chat-send" type="button">Send</button>
        <button class="setting-button composer-mic" id="chat-attach" type="button" title="Attach a picture">⊡</button>
        <button class="setting-button composer-mic" id="chat-mic" type="button" title="Hold to talk">●</button>
        <button class="setting-button composer-mic" id="voice-mode" type="button" title="Hands-free voice mode">◉</button>
      </div>
      <input type="file" id="chat-image-file" accept="image/*" capture="environment" hidden>
      <div class="status-line" id="chat-status"></div>
    </div>
  </main>

  <button id="drawer-backdrop" class="drawer-backdrop" type="button" aria-label="Close menu" hidden></button>

  <aside class="richard-drawer" id="richard-drawer" aria-label="Richard menu" aria-hidden="true">
    <section class="drawer-page is-active" data-drawer-page="menu">
      <div class="drawer-title" tabindex="-1"><b>&gt; MENU</b><span>RICHARD / HOME</span></div>
      <p class="drawer-copy">Talk to Richard on the home screen. Everything else lives here.</p>
      <nav class="drawer-nav" aria-label="Main menu">
        <button type="button" data-drawer-open="conversation">Conversation history <span>›</span></button>
        <button type="button" data-drawer-open="automations">Automations <span>›</span></button>
        <button type="button" data-drawer-open="memory">Memory <span>›</span></button>
        <p class="drawer-group-label">SETUP</p>
        <button type="button" data-drawer-open="configuration">Configuration <span>›</span></button>
        <button type="button" data-drawer-open="system">System <span>›</span></button>
      </nav>
    </section>

    <section class="drawer-page" data-drawer-page="configuration" hidden>
      <button type="button" class="drawer-back" data-drawer-back="menu">‹ Back to menu</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; CONFIGURATION</b><span>06 PAGES</span></div>
      <p class="drawer-copy">Settings are grouped by the part of Richard they change.</p>
      <nav class="drawer-nav" aria-label="Configuration pages">
        <button type="button" data-drawer-open="brain">Brain <span>LLM ›</span></button>
        <button type="button" data-drawer-open="personality">Personality <span>RICHARD ›</span></button>
        <button type="button" data-drawer-open="voice">Voice <span>STT + TTS ›</span></button>
        <button type="button" data-drawer-open="plugins">Plugins <span>CONNECTIONS ›</span></button>
        <button type="button" data-drawer-open="perception">Perception <span>CAMERA ›</span></button>
        <button type="button" data-drawer-open="network">Network <span>REALTIME + RELAY + WEB ›</span></button>
      </nav>
    </section>

    <section class="drawer-page" data-drawer-page="conversation" hidden>
      <button type="button" class="drawer-back" data-drawer-back="menu">‹ Back to menu</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; CONVERSATION HISTORY</b><span>session.chat</span></div>
      <div class="drawer-page-body" data-panel-content="chat-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="automations" hidden>
      <button type="button" class="drawer-back" data-drawer-back="menu">‹ Back to menu</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; AUTOMATIONS</b><span>system.control_loops</span></div>
      <div class="drawer-page-body" data-panel-content="control-loops-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="memory" hidden>
      <button type="button" class="drawer-back" data-drawer-back="menu">‹ Back to menu</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; MEMORY</b><span>system.memory</span></div>
      <div class="drawer-page-body" data-panel-content="memory-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="system" hidden>
      <button type="button" class="drawer-back" data-drawer-back="menu">‹ Back to menu</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; SYSTEM</b><span>system.runtime</span></div>
      <div class="drawer-page-body" data-panel-content="core-content serve-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="brain" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; BRAIN</b><span>system.brain.llm</span></div>
      <div class="drawer-page-body" data-panel-content="brain-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="personality" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; PERSONALITY</b><span>system.personality</span></div>
      <div class="drawer-page-body" data-panel-content="personality-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="voice" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; VOICE</b><span>system.voice</span></div>
      <div class="drawer-page-body" data-panel-content="voice-stt-content voice-tts-content voice-sample-content voice-effect-content voice-turn-content voice-mic-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="plugins" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; PLUGINS</b><span>system.plugins</span></div>
      <div class="drawer-page-body" data-panel-content="plugins-content home-assistant-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="perception" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; PERCEPTION</b><span>AMBIENT CAMERA</span></div>
      <div class="drawer-page-body" data-panel-content="perception-content perception-gallery-content"></div>
    </section>

    <section class="drawer-page" data-drawer-page="network" hidden>
      <button type="button" class="drawer-back" data-drawer-back="configuration">‹ Back to configuration</button>
      <div class="drawer-title" tabindex="-1"><b>&gt; NETWORK</b><span>system.network</span></div>
      <div class="drawer-page-body" data-panel-content="realtime-content relay-content web-content"></div>
    </section>
  </aside>

  <div class="legacy-panels" hidden>
    <!-- system.richard.core : live status readout -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="core-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.richard.core</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="core-content">
        <div class="status-display">
          <div class="status-item"><span class="status-label">runtime</span><span class="status-value" id="rt-runtime">—</span></div>
          <div class="status-item"><span class="status-label">version</span><span class="status-value" id="rt-version">—</span></div>
          <div class="status-item"><span class="status-label">brain</span><span class="status-value" id="rt-brain">—</span></div>
          <div class="status-item"><span class="status-label">relay</span><span class="status-value" id="rt-relay">—</span></div>
          <div class="status-item"><span class="status-label">satellites</span><span class="status-value" id="rt-sats">—</span></div>
          <div class="status-item"><span class="status-label">control loops</span><span class="status-value" id="rt-loops">—</span></div>
          <div class="status-item"><span class="status-label">web</span><span class="status-value" id="rt-web">—</span></div>
        </div>
      </div>
    </div>

    <!-- system.chat.richard -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="chat-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.chat.richard</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="chat-content">
        <p class="lede">Talk to Richard. A real session — he can control devices and use memory.</p>
        <div class="chat-log" id="chat-log"><div class="chat-empty">No messages yet. Say hello.</div></div>
      </div>
    </div>

    <!-- system.brain.llm -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="brain-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.brain.llm</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="brain-content">
        <p class="lede">The OpenAI-compatible endpoint Richard reasons against. Self-hosted llama.cpp by default.</p>
        <div class="setting-group"><label class="setting-label">Endpoint</label><input id="llm_endpoint" class="setting-input" placeholder="http://localhost:8080"></div>
        <div class="setting-group"><label class="setting-label">Model</label><input id="llm_model" class="setting-input" placeholder="local"></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">API key (optional)</label><input id="llm_api_key" type="password" class="setting-input" placeholder="(none)"></div>
          <div class="setting-group"><label class="setting-label">Timeout (s)</label><input id="llm_timeout" type="number" min="1" step="1" class="setting-input"></div>
        </div>
        <div class="button-row"><button class="setting-button" data-save="brain">Save changes</button><div class="status-line" id="brain-status"></div></div>
      </div>
    </div>

    <!-- system.personality.dials -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="personality-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.personality.dials</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="personality-content">
        <p class="lede">TARS-class disposition. Dials run 0–100.</p>
        <div class="setting-group"><label class="setting-label">Name</label><input id="personality.name" class="setting-input"></div>
        <div class="setting-group"><label class="setting-label spread">Humour <span class="dial-value" id="personality.humour.val">—</span></label><input id="personality.humour" type="range" min="0" max="100" class="dial-slider"></div>
        <div class="setting-group"><label class="setting-label spread">Honesty <span class="dial-value" id="personality.honesty.val">—</span></label><input id="personality.honesty" type="range" min="0" max="100" class="dial-slider"></div>
        <div class="setting-group"><label class="setting-label spread">Directness <span class="dial-value" id="personality.directness.val">—</span></label><input id="personality.directness" type="range" min="0" max="100" class="dial-slider"></div>
        <div class="setting-group"><label class="setting-label">Base character (system prompt)</label><textarea id="personality.system_prompt" class="setting-input" rows="6" placeholder="(default)"></textarea><span class="hint">Replaces the built-in TARS base; the dials still apply below it. Blank = default. {name} is substituted. Applies after a reboot.</span></div>
        <div class="button-row"><button class="setting-button" data-save="personality">Save changes</button><div class="status-line" id="personality-status"></div></div>
      </div>
    </div>

    <!-- system.voice.stt -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-stt-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.stt</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-stt-content">
        <p class="lede">Speech-to-text. Remote points at a Whisper server; local runs faster-whisper in-process. Restart to apply.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">STT engine</label><select id="voice.stt_engine" class="setting-input"><option value="local">local (faster-whisper)</option><option value="remote">remote (Whisper server)</option></select></div>
          <div class="setting-group"><label class="setting-label">STT model</label><input id="voice.stt_model" class="setting-input" placeholder="large-v3-turbo"></div>
        </div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">STT endpoint (remote only)</label><input id="voice.stt_endpoint" class="setting-input" placeholder="http://host:8005"></div>
          <div class="setting-group"><label class="setting-label">Language</label><input id="voice.language" class="setting-input" placeholder="auto"><span class="hint">STT hint and TTS voice selection. auto = detect per utterance.</span></div>
        </div>
        <div class="button-row"><button class="setting-button" data-save="voice-stt">Save changes</button><div class="status-line" id="voice-stt-status"></div></div>
      </div>
    </div>

    <!-- system.voice.tts -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-tts-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.tts</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-tts-content">
        <p class="lede">Text-to-speech. Remote is the Qwen3-TTS server on the GPU box; kokoro and piper run in-process. Restart to apply.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">TTS engine</label><select id="voice.tts_engine" class="setting-input"><option value="kokoro">kokoro</option><option value="piper">piper</option><option value="remote">remote (GPU server)</option></select></div>
          <div class="setting-group"><label class="setting-label">TTS voice</label><input id="voice.tts_voice" class="setting-input" list="tts-voice-options" placeholder="clap1"><datalist id="tts-voice-options"></datalist></div>
        </div>
        <div class="setting-group"><label class="setting-label">TTS endpoint (remote only)</label><input id="voice.tts_endpoint" class="setting-input" placeholder="http://host:8091"></div>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="voice.tts_streaming"><label for="voice.tts_streaming">Stream speech sentence-by-sentence</label><span class="checkmark"></span></div></div>
        <div data-remote-only>
          <div class="setting-group"><label class="setting-label">Qwen3-TTS request (remote only)</label><span class="hint">Fields sent to the server. Blank model = omit the field (vLLM-Omni serves one checkpoint).</span></div>
          <div class="field-row">
            <div class="setting-group"><label class="setting-label">Model</label><input id="voice.tts_model" class="setting-input" placeholder="(blank)"></div>
            <div class="setting-group"><label class="setting-label">Language</label><input id="voice.tts_language" class="setting-input" placeholder="Italian / English / (server default)"></div>
          </div>
          <div class="setting-group"><label class="setting-label">Instructions</label><input id="voice.tts_instructions" class="setting-input" placeholder="(none) style / emotion hint"></div>
          <div class="field-row">
            <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="voice.tts_xvec_only"><label for="voice.tts_xvec_only">x-vector only (timbre only, native prosody)</label><span class="checkmark"></span></div></div>
            <div class="setting-group"><label class="setting-label">Task type</label><select id="voice.tts_task_type" class="setting-input"><option value="">(server default)</option><option value="Base">Base</option><option value="CustomVoice">CustomVoice</option><option value="VoiceDesign">VoiceDesign</option></select></div>
          </div>
        </div>
        <div class="button-row"><button class="setting-button" data-save="voice-tts">Save changes</button><div class="status-line" id="voice-tts-status"></div></div>
      </div>
    </div>

    <!-- system.voice.sample -->
    <div class="terminal-section" data-remote-only>
      <div class="terminal-header-line" data-target="voice-sample-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.sample</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-sample-content">
        <p class="lede">Upload a reference clip to the TTS server as a named voice. Mono, 8–15 s, speech only; the file goes up unchanged and the server resamples. "Use" saves it as the TTS voice; restart to hear it.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Audio file</label><input id="voice-sample-file" type="file" accept="audio/*" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Voice name</label><input id="voice-sample-name" class="setting-input" placeholder="clap1v" maxlength="32"></div>
        </div>
        <div class="setting-group"><label class="setting-label">Transcript (optional; used by in-context mode, ignored by x-vector)</label><textarea id="voice-sample-transcript" class="setting-input" rows="2" placeholder="What the clip says"></textarea></div>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="voice-sample-consent"><label for="voice-sample-consent">I own this recording or have permission to use it</label><span class="checkmark"></span></div></div>
        <div class="button-row"><button class="setting-button" id="voice-sample-upload">Upload</button><button class="setting-button small" id="voice-sample-restart">Restart to apply</button><div class="status-line" id="voice-sample-status"></div></div>
        <div class="setting-group" style="margin-top:0.85rem;">
          <label class="setting-label">Voices on the server</label>
          <div class="entry-list" id="voice-list"><div class="empty-message">Remote engine only.</div></div>
        </div>
      </div>
    </div>

    <!-- system.voice.effect -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-effect-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.effect</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-effect-content">
        <p class="lede">Post-processing on every spoken sentence, for any engine. Robot: speaker colouring, ring modulation and bit crush. Restart to apply.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Effect</label><select id="voice.tts_effect" class="setting-input"><option value="none">none</option><option value="robot">robot</option></select></div>
          <div class="setting-group"><label class="setting-label">Tone (Hz, 20–200)</label><input id="voice.tts_effect_tone" type="number" min="20" max="200" step="1" class="setting-input"></div>
        </div>
        <div class="setting-group"><label class="setting-label spread">Strength <span class="dial-value" id="voice.tts_effect_strength.val">—</span></label><input id="voice.tts_effect_strength" type="range" min="0" max="100" class="dial-slider"></div>
        <div class="button-row"><button class="setting-button" data-save="voice-effect">Save changes</button><div class="status-line" id="voice-effect-status"></div></div>
      </div>
    </div>

    <!-- system.voice.turns -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-turn-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.turns</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-turn-content">
        <p class="lede">When Richard decides you have finished speaking.</p>
        <div class="setting-group"><label class="setting-label">Realtime endpoint silence (ms)</label><input id="voice.endpoint_silence_ms" type="number" min="100" max="3000" step="50" class="setting-input"><span class="hint">Trailing silence that ends an utterance on the realtime API (Reachy, browser voice mode).</span></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">VAD aggressiveness (0–3)</label><input id="voice.vad_aggressiveness" type="number" min="0" max="3" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Silence (ms)</label><input id="voice.silence_ms" type="number" min="0" class="setting-input"></div>
        </div>
        <span class="hint">VAD and silence apply to satellites and the local voice loop, not to the realtime API.</span>
        <div class="button-row"><button class="setting-button" data-save="voice-turn">Save changes</button><div class="status-line" id="voice-turn-status"></div></div>
      </div>
    </div>

    <!-- system.voice.mic -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="voice-mic-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.voice.mic</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="voice-mic-content">
        <p class="lede">Only for <code>richard voice</code> on a machine with a microphone. serve does not use these.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Sample rate</label><input id="voice.samplerate" type="number" min="8000" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Input device</label><input id="voice.input_device" class="setting-input" placeholder="(default)"></div>
        </div>
        <div class="setting-group"><label class="setting-label">Output device</label><input id="voice.output_device" class="setting-input" placeholder="(default)"></div>
        <div class="button-row"><button class="setting-button" data-save="voice-mic">Save changes</button><div class="status-line" id="voice-mic-status"></div></div>
      </div>
    </div>

    <!-- system.plugins.registry -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="plugins-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.plugins.registry</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="plugins-content">
        <p class="lede">Connections Richard can use. Installed plugins come from Python entry points (richard plugins install). Enabling or disabling one takes effect after a restart.</p>
        <div class="entry-list" id="plugins-list"><div class="empty-message">Loading…</div></div>
        <div class="button-row"><button class="setting-button small" id="plugins-refresh">Refresh</button><button class="setting-button small" id="plugins-restart">Restart to apply</button><div class="status-line" id="plugins-status"></div></div>
      </div>
    </div>

    <!-- system.home_assistant.api -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="home-assistant-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.home_assistant.api</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="home-assistant-content">
        <p class="lede">Home Assistant plugin: entities and service calls. Use a long-lived access token from your Home Assistant profile. Save, test the connection, then restart Richard to apply.</p>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="home_assistant.enabled"><label for="home_assistant.enabled">Home Assistant enabled</label><span class="checkmark"></span></div></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">IP / hostname</label><input id="home_assistant.host" class="setting-input" placeholder="192.168.1.20 or homeassistant.local"></div>
          <div class="setting-group"><label class="setting-label">API port</label><input id="home_assistant.port" type="number" min="1" max="65535" class="setting-input" placeholder="8123"></div>
        </div>
        <div class="setting-group"><label class="setting-label">Long-lived access token</label><input id="home_assistant.token" type="password" class="setting-input" placeholder="(unset)" autocomplete="off"></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Timeout (s)</label><input id="home_assistant.timeout" type="number" min="1" max="300" step="1" class="setting-input"></div>
          <div class="setting-group">
            <div class="checkbox-btn"><input type="checkbox" id="home_assistant.use_https"><label for="home_assistant.use_https">Use HTTPS</label><span class="checkmark"></span></div>
            <div class="checkbox-btn"><input type="checkbox" id="home_assistant.verify_ssl"><label for="home_assistant.verify_ssl">Verify HTTPS certificate</label><span class="checkmark"></span></div>
          </div>
        </div>
        <div class="button-row">
          <button class="setting-button" data-save="home-assistant">Save changes</button>
          <button class="setting-button small" id="home-assistant-test">Test connection</button>
          <button class="setting-button small" id="home-assistant-restart">Restart to apply</button>
          <div class="status-line" id="home-assistant-status"></div>
        </div>
        <div class="setting-group" style="margin-top:0.85rem;">
          <label class="setting-label spread">Home Assistant entities <span id="home-assistant-entity-count">—</span></label>
          <input id="home-assistant-entity-filter" class="setting-input" placeholder="Filter by name, entity id, domain, or state">
          <div class="lede" id="home-assistant-domain-summary" style="margin:0;"></div>
          <div class="entry-list" id="home-assistant-entity-list" style="max-height:320px;overflow-y:auto;"><div class="empty-message">Test the connection to load entities.</div></div>
        </div>
      </div>
    </div>

    <!-- system.perception.ambient -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="perception-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.perception.ambient</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="perception-content">
        <p class="lede">Richard watches the camera continuously at almost no cost and is told only about transitions: someone arrived, left, was recognised, the scene changed. Enable the plugin, allow the camera on this device, and restart to apply settings.</p>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="perception.plugin_enabled"><label for="perception.plugin_enabled">Perception plugin enabled</label><span class="checkmark"></span></div><span class="hint">Disabled means invisible to Richard: no events, no camera tool, no stream accepted.</span></div>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="perception.identity_enabled"><label for="perception.identity_enabled">Recognise enrolled people</label><span class="checkmark"></span></div><span class="hint">Opt-in. Faces are matched against the local gallery below; nothing leaves this box.</span></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Quiet hours</label><input id="perception.quiet_hours" class="setting-input" placeholder="23:00-07:30"><span class="hint">No events reach Richard in this window.</span></div>
          <div class="setting-group"><label class="setting-label">Sensitivity</label><input id="perception.sensitivity" class="setting-input" type="number" min="0" max="100"></div>
          <div class="setting-group"><label class="setting-label">Cooldown (s)</label><input id="perception.cooldown_s" class="setting-input" type="number" min="0"></div>
        </div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Stream frames per second</label><input id="perception.stream_fps" class="setting-input" type="number" min="0.5" max="10" step="0.5"></div>
          <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="perception.keep_thumbnails"><label for="perception.keep_thumbnails">Keep a thumbnail per event</label><span class="checkmark"></span></div></div>
        </div>
        <div class="button-row"><button class="setting-button" id="perception-save">Save changes</button><button class="setting-button small" id="perception-restart">Restart to apply</button><div class="status-line" id="perception-status"></div></div>
        <label class="setting-label spread">Live <span id="perception-sources">—</span></label>
        <img id="perception-latest" class="chat-thumb" alt="latest frame" hidden>
        <label class="setting-label spread">Events <span id="perception-presence">nobody in view</span></label>
        <div class="entry-list" id="perception-events" style="max-height:260px;overflow-y:auto;"><div class="empty-message">No events yet.</div></div>
      </div>
    </div>

    <!-- system.perception.gallery -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="perception-gallery-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.perception.gallery</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="perception-gallery-content">
        <p class="lede">People Richard may recognise. Enrolment takes three snapshots from this device's camera; each entry can be deleted at any time and is then forgotten for good.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Name</label><input id="perception-enrol-name" class="setting-input" placeholder="Matteo"></div>
          <div class="setting-group"><label class="setting-label">&nbsp;</label><button class="setting-button" id="perception-enrol">Enrol from camera</button></div>
        </div>
        <div class="status-line" id="perception-gallery-status"></div>
        <div class="entry-list" id="perception-gallery"><div class="empty-message">Nobody enrolled.</div></div>
      </div>
    </div>

    <!-- system.control_loops.monitor -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="control-loops-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.control_loops.monitor</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="control-loops-content">
        <p class="lede">Persistent monitors for Home Assistant entities. State changes wake Richard so he can evaluate your trigger.</p>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Loop name</label><input id="control-loop-name" class="setting-input" placeholder="Workshop safety"></div>
          <div class="setting-group"><label class="setting-label">Poll every (s)</label><input id="control-loop-interval" type="number" min="5" max="86400" value="30" class="setting-input"></div>
        </div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Kind</label><select id="loop-kind" class="setting-input"><option value="change">on change</option><option value="scheduled">scheduled</option></select></div>
          <div class="setting-group">
            <label class="setting-label">Schedule</label>
            <select id="loop-schedule-mode" class="setting-input" hidden><option value="at">daily at</option><option value="every">every</option><option value="once">once at</option></select>
            <input id="loop-schedule-at" type="time" class="setting-input" hidden>
            <input id="loop-schedule-days" type="text" class="setting-input" placeholder="days e.g. mon,fri (optional)" hidden>
            <input id="loop-schedule-every" type="number" min="1" class="setting-input" placeholder="minutes" hidden>
            <input id="loop-schedule-once" type="datetime-local" class="setting-input" hidden>
          </div>
        </div>
        <div class="setting-group"><label class="setting-label">Targets</label><input id="control-loop-targets" class="setting-input" placeholder="Desk Lamp, sensor.kitchen_temperature"><span class="hint">Comma-separated Home Assistant entity ids or friendly names. Optional for scheduled loops.</span></div>
        <div class="setting-group"><label class="setting-label">Trigger description + action</label><textarea id="control-loop-trigger" class="setting-input" rows="4" placeholder="If the temperature exceeds 30°C while the fan is off, turn the fan on and tell me."></textarea></div>
        <div class="button-row"><button class="setting-button" id="control-loop-add">Create loop</button><button class="setting-button small" id="control-loop-refresh">Refresh</button><div class="status-line" id="control-loop-status"></div></div>
        <div class="entry-list" id="control-loop-list"><div class="empty-message">No control loops.</div></div>
        <div class="setting-group" style="margin-top:1rem;"><label class="setting-label">Richard event inbox</label><span class="hint">LLM responses generated after monitored changes.</span></div>
        <div class="entry-list" id="control-notification-list"><div class="empty-message">No control-loop notifications.</div></div>
      </div>
    </div>

    <!-- system.realtime.api -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="realtime-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.realtime.api</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="realtime-content">
        <p class="lede">The OpenAI-style realtime WebSocket the Reachy Conversation App and the browser voice mode connect to (wss://host:port/v1/realtime). Restart to apply.</p>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="realtime.enabled"><label for="realtime.enabled">Realtime API enabled</label><span class="checkmark"></span></div></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Host</label><input id="realtime.host" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Port</label><input id="realtime.port" type="number" class="setting-input"></div>
        </div>
        <div class="setting-group"><label class="setting-label">Token</label><input id="realtime.token" class="setting-input" placeholder="(open — set one to require it)" autocomplete="off"></div>
        <div class="button-row"><button class="setting-button" data-save="realtime">Save changes</button><div class="status-line" id="realtime-status"></div></div>
      </div>
    </div>

    <!-- system.relay.mesh -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="relay-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.relay.mesh</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="relay-content">
        <p class="lede">The WebSocket server hubs connect to for multi-room voice. Restart serve after changing host or port.</p>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="satellite.enabled"><label for="satellite.enabled">Relay enabled</label><span class="checkmark"></span></div></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Host</label><input id="satellite.host" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Port</label><input id="satellite.port" type="number" class="setting-input"></div>
        </div>
        <div class="button-row"><button class="setting-button" data-save="relay">Save changes</button><div class="status-line" id="relay-status"></div></div>
      </div>
    </div>

    <!-- system.web.access -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="web-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.web.access</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="web-content">
        <p class="lede">This page. Restart serve after changing host or port.</p>
        <div class="setting-group"><div class="checkbox-btn"><input type="checkbox" id="web.enabled"><label for="web.enabled">Web UI enabled</label><span class="checkmark"></span></div></div>
        <div class="field-row">
          <div class="setting-group"><label class="setting-label">Host</label><input id="web.host" class="setting-input"></div>
          <div class="setting-group"><label class="setting-label">Port</label><input id="web.port" type="number" class="setting-input"></div>
        </div>
        <div class="button-row"><button class="setting-button" data-save="web">Save changes</button><div class="status-line" id="web-status"></div></div>
      </div>
    </div>

    <!-- system.memory.recall -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="memory-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.memory.recall</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="memory-content">
        <p class="lede">Facts Richard remembers about you across conversations.</p>
        <div class="row-add">
          <input id="memory-text" class="setting-input" placeholder="Add a fact to remember…">
          <button class="setting-button" id="memory-add">Remember</button>
        </div>
        <div class="entry-list" id="memory-list" style="margin-top:0.75rem;"><div class="empty-message">No memories yet.</div></div>
        <div class="button-row" id="memory-toggle-row" hidden><button class="setting-button small" id="memory-toggle">Show all</button><span class="entry-sub" id="memory-count"></span></div>
        <div class="status-line" id="memory-status" style="margin-top:0.5rem;"></div>
      </div>
    </div>

    <!-- system.serve.control -->
    <div class="terminal-section">
      <div class="terminal-header-line" data-target="serve-content">
        <span class="terminal-prompt">&gt; </span>
        <span class="terminal-command window-title">system.serve.control</span>
        <span class="window-control is-open">[_]</span>
      </div>
      <div class="window-content" id="serve-content">
        <p class="lede">Restart the relay, realtime and web servers to apply changes that need it: host/port changes, STT/TTS engines and endpoints, the voice effect, personality, plugin enable/disable and plugin settings. Connections drop for a few seconds.</p>
        <div class="button-row"><button class="setting-button" id="serve-reboot">Reboot serve</button><div class="status-line" id="serve-status"></div></div>
      </div>
    </div>
  </div>

  <footer hidden>
    <div class="info-bar">
      <span><span class="terminal-prompt smaller">&gt; </span><span class="smaller">system.info</span></span>
      <span class="smaller">version <b id="foot-version">—</b></span>
      <span class="smaller">satellites <b id="foot-sats">—</b></span>
    </div>
  </footer>
</div>

<script src="/realtime-client.js"></script>
<script>
const FIELDS = [
  {id:'llm_endpoint', path:['llm_endpoint'], t:'url', sec:'brain', req:true},
  {id:'llm_model', path:['llm_model'], t:'text', sec:'brain'},
  {id:'llm_api_key', path:['llm_api_key'], t:'text', sec:'brain', nullable:true},
  {id:'llm_timeout', path:['llm_timeout'], t:'num', sec:'brain', req:true, min:1},
  {id:'personality.name', path:['personality','name'], t:'text', sec:'personality'},
  {id:'personality.humour', path:['personality','humour'], t:'dial', sec:'personality'},
  {id:'personality.honesty', path:['personality','honesty'], t:'dial', sec:'personality'},
  {id:'personality.directness', path:['personality','directness'], t:'dial', sec:'personality'},
  {id:'personality.system_prompt', path:['personality','system_prompt'], t:'textarea', sec:'personality'},
  {id:'voice.stt_engine', path:['voice','stt_engine'], t:'sel', sec:'voice-stt'},
  {id:'voice.stt_model', path:['voice','stt_model'], t:'text', sec:'voice-stt'},
  {id:'voice.stt_endpoint', path:['voice','stt_endpoint'], t:'url', sec:'voice-stt', nullable:true},
  {id:'voice.language', path:['voice','language'], t:'text', sec:'voice-stt'},
  {id:'voice.tts_engine', path:['voice','tts_engine'], t:'sel', sec:'voice-tts'},
  {id:'voice.tts_voice', path:['voice','tts_voice'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_endpoint', path:['voice','tts_endpoint'], t:'url', sec:'voice-tts', nullable:true},
  {id:'voice.tts_streaming', path:['voice','tts_streaming'], t:'bool', sec:'voice-tts'},
  {id:'voice.tts_model', path:['voice','tts_model'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_language', path:['voice','tts_language'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_instructions', path:['voice','tts_instructions'], t:'text', sec:'voice-tts'},
  {id:'voice.tts_xvec_only', path:['voice','tts_xvec_only'], t:'bool', sec:'voice-tts'},
  {id:'voice.tts_task_type', path:['voice','tts_task_type'], t:'sel', sec:'voice-tts'},
  {id:'voice.tts_effect', path:['voice','tts_effect'], t:'sel', sec:'voice-effect'},
  {id:'voice.tts_effect_strength', path:['voice','tts_effect_strength'], t:'dial', sec:'voice-effect'},
  {id:'voice.tts_effect_tone', path:['voice','tts_effect_tone'], t:'num', sec:'voice-effect', min:20, max:200},
  {id:'voice.endpoint_silence_ms', path:['voice','endpoint_silence_ms'], t:'num', sec:'voice-turn', min:100, max:3000},
  {id:'voice.vad_aggressiveness', path:['voice','vad_aggressiveness'], t:'num', sec:'voice-turn', min:0, max:3},
  {id:'voice.silence_ms', path:['voice','silence_ms'], t:'num', sec:'voice-turn', min:0},
  {id:'voice.samplerate', path:['voice','samplerate'], t:'num', sec:'voice-mic', min:8000},
  {id:'voice.input_device', path:['voice','input_device'], t:'text', sec:'voice-mic', nullable:true},
  {id:'voice.output_device', path:['voice','output_device'], t:'text', sec:'voice-mic', nullable:true},
  {id:'home_assistant.enabled', path:['home_assistant','enabled'], t:'bool', sec:'home-assistant'},
  {id:'home_assistant.host', path:['home_assistant','host'], t:'text', sec:'home-assistant', req:true},
  {id:'home_assistant.port', path:['home_assistant','port'], t:'port', sec:'home-assistant'},
  {id:'home_assistant.token', path:['home_assistant','token'], t:'text', sec:'home-assistant', nullable:true},
  {id:'home_assistant.timeout', path:['home_assistant','timeout'], t:'num', sec:'home-assistant', req:true, min:1, max:300},
  {id:'home_assistant.use_https', path:['home_assistant','use_https'], t:'bool', sec:'home-assistant'},
  {id:'home_assistant.verify_ssl', path:['home_assistant','verify_ssl'], t:'bool', sec:'home-assistant'},
  {id:'satellite.enabled', path:['satellite','enabled'], t:'bool', sec:'relay'},
  {id:'satellite.host', path:['satellite','host'], t:'text', sec:'relay'},
  {id:'satellite.port', path:['satellite','port'], t:'port', sec:'relay'},
  {id:'web.enabled', path:['web','enabled'], t:'bool', sec:'web'},
  {id:'web.host', path:['web','host'], t:'text', sec:'web'},
  {id:'web.port', path:['web','port'], t:'port', sec:'web'},
  {id:'realtime.enabled', path:['realtime','enabled'], t:'bool', sec:'realtime'},
  {id:'realtime.host', path:['realtime','host'], t:'text', sec:'realtime'},
  {id:'realtime.port', path:['realtime','port'], t:'port', sec:'realtime'},
  {id:'realtime.token', path:['realtime','token'], t:'text', sec:'realtime'},
];
const BY_ID = Object.fromEntries(FIELDS.map(f => [f.id, f]));
const SAVABLE = ['brain','personality','voice-stt','voice-tts','voice-effect','voice-turn','voice-mic','home-assistant','relay','web','realtime'];
let baseline = {};
const {RealtimePresenceController, SharedCamera, FrameUploader, createBrowserSourceId} = RichardRealtimeClient;
const pageSourceId = createBrowserSourceId(typeof crypto === 'undefined' ? null : crypto);

const $ = id => document.getElementById(id);
function getPath(o, p){ return p.reduce((a,k) => (a == null ? a : a[k]), o); }
function setPath(o, p, v){ if (p.length === 1) { o[p[0]] = v; } else { o[p[0]] = o[p[0]] || {}; o[p[0]][p[1]] = v; } }
function hm(){ const d = new Date(); return String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0'); }
function esc(s){ const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }

async function getJSON(url, options){ const r = await fetch(url, options); if (!r.ok) throw new Error(await r.text()); return r.json(); }
async function sendJSON(url, method, body){
  const r = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const text = await r.text();
  const data = text ? JSON.parse(text) : {};
  if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

function getForm(f){ const el = $(f.id); if (!el) return undefined; return f.t === 'bool' ? el.checked : el.value; }
function setForm(f, cfg){
  const el = $(f.id); if (!el) return;
  const v = getPath(cfg, f.path);
  if (f.t === 'bool') { el.checked = !!v; }
  else if (f.t === 'dial') { el.value = (v == null ? 0 : v); const o = $(f.id + '.val'); if (o) o.textContent = el.value; }
  else { el.value = (v == null ? '' : String(v)); }
  baseline[f.id] = getForm(f);
}
function typedValue(f){
  const el = $(f.id);
  if (f.t === 'bool') return el.checked;
  if (f.t === 'num' || f.t === 'port' || f.t === 'dial') return Number(el.value);
  return el.value;
}
function isDirty(f){ return getForm(f) !== baseline[f.id]; }
function validate(f){
  const el = $(f.id); if (!el || f.t === 'bool' || f.t === 'dial' || f.t === 'sel') return null;
  const v = el.value.trim();
  if (f.t === 'url') {
    if (v === '') return f.req ? 'required' : null;
    if (!(v.startsWith('http://') || v.startsWith('https://'))) return 'must start with http:// or https://';
    return null;
  }
  if (f.t === 'num') {
    if (v === '') return f.req ? 'required' : null;
    const n = Number(v);
    if (!isFinite(n)) return 'must be a number';
    if (f.min != null && n < f.min) return 'min ' + f.min;
    if (f.max != null && n > f.max) return 'max ' + f.max;
    return null;
  }
  if (f.t === 'port') {
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1 || n > 65535) return 'port must be 1–65535';
    return null;
  }
  if (f.req && v === '') return 'required';
  return null;
}

function setStatus(sec, msg, kind){ const el = $(sec + '-status'); if (!el) return; el.textContent = msg; el.className = 'status-line' + (kind ? ' ' + kind : ''); }
function fieldsOf(sec){ return FIELDS.filter(f => f.sec === sec); }
function reflectDirty(sec){
  if (fieldsOf(sec).some(isDirty)) setStatus(sec, 'unsaved changes', '');
  else setStatus(sec, '', '');
}

function applyConfig(cfg){
  for (const f of FIELDS) setForm(f, cfg);
  for (const f of FIELDS) { const el = $(f.id); if (el) el.classList.remove('invalid'); }
  if (cfg.prompt_default) { const t = $('personality.system_prompt'); if (t) t.placeholder = cfg.prompt_default; }
  if (cfg.home_assistant) {
    const t = $('home_assistant.token');
    if (t) t.placeholder = cfg.home_assistant.token_configured ? '(configured — enter to replace)' : '(unset)';
  }
  reflectRemoteOnly();
  renderReadout(cfg);
}

async function rebootServe(){
  if (!confirm('Reboot serve now? Connections drop for a few seconds while it restarts.')) return;
  const btn = $('serve-reboot'); btn.disabled = true;
  setStatus('serve', 'rebooting…', '');
  try { await sendJSON('/api/restart', 'POST'); } catch (e) { /* the connection may drop mid-restart — expected */ }
  let tries = 0;
  const tick = async () => {
    tries++;
    try { await getJSON('/api/status'); setStatus('serve', 'back online · reloading…', 'ok'); setTimeout(() => location.reload(), 800); }
    catch (e) {
      if (tries > 30) { setStatus('serve', 'still rebooting — check the host', 'err'); btn.disabled = false; }
      else setTimeout(tick, 1000);
    }
  };
  setTimeout(tick, 1500);
}

function renderReadout(cfg){
  $('rt-runtime').textContent = 'online';
  $('rt-brain').textContent = cfg.llm_model + ' @ ' + cfg.llm_endpoint;
  $('rt-relay').textContent = (cfg.satellite.enabled ? '' : '(disabled) ') + cfg.satellite.host + ':' + cfg.satellite.port;
  $('rt-web').textContent = (cfg.web.enabled ? '' : '(disabled) ') + cfg.web.host + ':' + cfg.web.port;
}

async function saveSection(sec){
  const fields = fieldsOf(sec);
  for (const f of fields) { const e = validate(f); const el = $(f.id); if (el) el.classList.toggle('invalid', !!e); if (e) { setStatus(sec, f.id.split('.').pop() + ': ' + e, 'err'); return; } }
  const patch = {}; let n = 0;
  for (const f of fields) { if (isDirty(f)) { setPath(patch, f.path, typedValue(f)); n++; } }
  if (n === 0) { setStatus(sec, 'no changes', ''); return; }
  setStatus(sec, 'saving…', '');
  try {
    const data = await sendJSON('/api/config', 'PUT', patch);
    applyConfig(data.config);
    if (sec.startsWith('voice-')) await refreshCueBank();
    const saved = 'saved ' + data.changed.length + ' field' + (data.changed.length === 1 ? '' : 's') + ' · ' + hm();
    if (sec === 'home-assistant') await refreshHomeAssistant(saved);
    else setStatus(sec, saved, 'ok');
  } catch (e) { setStatus(sec, 'save failed: ' + e.message, 'err'); }
}

let homeAssistantEntities = [];
function renderHomeAssistantEntities(data){
  homeAssistantEntities = data.entities || [];
  $('home-assistant-entity-count').textContent = data.connected ? data.entity_count + ' total' : '—';
  const domains = Object.entries(data.domains || {}).sort((a,b) => b[1] - a[1]);
  $('home-assistant-domain-summary').textContent = domains.length
    ? domains.slice(0, 14).map(([name,count]) => name + ' ' + count).join(' · ') + (domains.length > 14 ? ' · …' : '')
    : '';
  filterHomeAssistantEntities();
}
function filterHomeAssistantEntities(){
  const box = $('home-assistant-entity-list');
  const query = $('home-assistant-entity-filter').value.trim().toLowerCase();
  const matches = homeAssistantEntities.filter(entity => !query ||
    entity.entity_id.toLowerCase().includes(query) || entity.name.toLowerCase().includes(query) ||
    entity.domain.toLowerCase().includes(query) || entity.state.toLowerCase().includes(query));
  if (!matches.length) {
    box.innerHTML = '<div class="empty-message">' + (homeAssistantEntities.length ? 'No matching entities.' : 'No entities loaded.') + '</div>';
    return;
  }
  const shown = matches.slice(0, 100);
  box.innerHTML = shown.map(entity => {
    const unavailable = entity.state === 'unavailable' || entity.state === 'unknown';
    return '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(entity.name) +
      '</div><div class="entry-sub">' + esc(entity.entity_id) + '</div></div><div class="entry-sub ' +
      (unavailable ? 'offline' : 'online') + '">' + esc(entity.state) + '</div></div>';
  }).join('') + (matches.length > shown.length
    ? '<div class="empty-message">Showing 100 of ' + matches.length + ' matches — refine the filter.</div>' : '');
}
async function refreshHomeAssistant(prefix){
  const lead = prefix ? prefix + ' · ' : '';
  setStatus('home-assistant', lead + 'testing…', '');
  try {
    const data = await getJSON('/api/home-assistant');
    renderHomeAssistantEntities(data);
    if (data.connected) {
      setStatus('home-assistant', lead + 'connected · ' + data.entity_count + ' entities' + (prefix ? ' · restart to apply' : ''), 'ok');
    } else {
      setStatus('home-assistant', lead + (data.error || 'not connected'), data.enabled ? 'err' : '');
    }
    return data;
  } catch (e) {
    renderHomeAssistantEntities({entities:[], domains:{}, connected:false});
    setStatus('home-assistant', lead + 'test failed: ' + e.message, 'err');
    return null;
  }
}

function errorText(e){ const m = e && e.message ? e.message : String(e); try { return JSON.parse(m).error || m; } catch (_) { return m; } }

function reflectRemoteOnly(){
  const engine = $('voice.tts_engine');
  const remote = !!engine && engine.value === 'remote';
  document.querySelectorAll('[data-remote-only]').forEach(el => el.classList.toggle('remote-only-hidden', !remote));
}

async function loadVoices(){
  const options = $('tts-voice-options'); const box = $('voice-list');
  const engine = $('voice.tts_engine');
  if (!engine || engine.value !== 'remote') { options.innerHTML = ''; box.innerHTML = '<div class="empty-message">Remote engine only.</div>'; return; }
  try {
    const data = await getJSON('/api/voices');
    const uploaded = Object.fromEntries((data.uploaded || []).map(u => [u.name, u]));
    options.innerHTML = data.voices.map(v => '<option value="' + esc(v) + '"></option>').join('');
    box.innerHTML = data.voices.length ? data.voices.map(v => {
      const meta = uploaded[v] ? ('uploaded' + (uploaded[v].ref_text ? ' · ' + uploaded[v].ref_text.slice(0, 60) : '')) : 'built-in';
      return '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(v) + '</div><div class="entry-sub">' + esc(meta) + '</div></div>' +
        '<button class="setting-button small" data-use-voice="' + esc(v) + '">Use</button></div>';
    }).join('') : '<div class="empty-message">No voices on the server.</div>';
  } catch (e) {
    options.innerHTML = '';
    box.innerHTML = '<div class="empty-message">' + esc(errorText(e)) + '</div>';
  }
}

async function useVoice(name){
  // "Use" means use: set the voice, save the TTS window, and say so next to the list.
  const el = $('voice.tts_voice'); if (!el) return;
  el.value = name;
  el.dispatchEvent(new Event('input'));
  setStatus('voice-sample', 'saving ' + name + '…', '');
  await saveSection('voice-tts');
  if (isDirty(BY_ID['voice.tts_voice'])) setStatus('voice-sample', 'could not save ' + name + ' (see the text-to-speech window)', 'err');
  else setStatus('voice-sample', name + ' is now the TTS voice · ' + hm() + ' · restart to apply', 'ok');
}

function readFileBase64(file){
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(reader.error || new Error('could not read the file'));
    reader.readAsDataURL(file);
  });
}

async function uploadVoiceSample(){
  const file = ($('voice-sample-file').files || [])[0];
  const name = $('voice-sample-name').value.trim();
  if (!file) { setStatus('voice-sample', 'choose an audio file', 'err'); return; }
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) { setStatus('voice-sample', 'name: letters, digits, _ and -, at most 32', 'err'); return; }
  if (!$('voice-sample-consent').checked) { setStatus('voice-sample', 'confirm you may use this recording', 'err'); return; }
  setStatus('voice-sample', 'uploading…', '');
  try {
    const audio_base64 = await readFileBase64(file);
    const data = await sendJSON('/api/voices', 'POST', {name, transcript: $('voice-sample-transcript').value.trim(), filename: file.name, audio_base64});
    setStatus('voice-sample', 'uploaded ' + data.uploaded + ' · ' + hm(), 'ok');
    await loadVoices();
    await useVoice(data.uploaded);
  } catch (e) { setStatus('voice-sample', 'upload failed: ' + e.message, 'err'); }
}

function renderPlugins(rows){
  const box = $('plugins-list');
  if (!rows.length) { box.innerHTML = '<div class="empty-message">No plugins installed.</div>'; return; }
  box.innerHTML = rows.map(r => {
    const state = r.running === 'unknown' ? '' : ' · running: ' + esc(r.running) + (r.error ? ' (' + esc(r.error) + ')' : '');
    return '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(r.name) + ' <span class="entry-sub">' + esc(r.version) + '</span></div>' +
      '<div class="entry-sub">' + (r.configured ? 'enabled' : 'disabled') + state + '</div></div>' +
      '<button class="setting-button small" data-plugin-toggle="' + esc(r.name) + '" data-plugin-enabled="' + (r.configured ? '1' : '0') + '">' + (r.configured ? 'Disable' : 'Enable') + '</button></div>';
  }).join('');
}

async function loadPlugins(){
  try { renderPlugins((await getJSON('/api/plugins')).plugins); }
  catch (e) { $('plugins-list').innerHTML = '<div class="empty-message">' + esc(errorText(e)) + '</div>'; }
}

async function togglePlugin(name, enabled){
  setStatus('plugins', (enabled ? 'enabling ' : 'disabling ') + name + '…', '');
  try {
    const data = await sendJSON('/api/plugins', 'PUT', {name, enabled});
    renderPlugins(data.plugins);
    setStatus('plugins', name + (enabled ? ' enabled' : ' disabled') + ' · restart to apply · ' + hm(), 'ok');
  } catch (e) { setStatus('plugins', 'failed: ' + e.message, 'err'); }
}

const MEMORY_COLLAPSED_LIMIT = 12;
let memoryCache = [];
let memoriesExpanded = false;
function renderMemories(memories){
  memoryCache = memories;
  const box = $('memory-list');
  const toggleRow = $('memory-toggle-row');
  const toggle = $('memory-toggle');
  if (!memories.length) {
    memoriesExpanded = false;
    box.innerHTML = '<div class="empty-message">No memories yet.</div>';
    toggleRow.hidden = true;
    return;
  }
  if (memories.length <= MEMORY_COLLAPSED_LIMIT) memoriesExpanded = false;
  const visible = memoriesExpanded ? memories : memories.slice(-MEMORY_COLLAPSED_LIMIT);
  box.innerHTML = visible.map(m =>
    '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(m.text) +
    '</div><div class="entry-sub">id ' + m.id + '</div></div>' +
    '<button class="setting-button small" data-forget="' + m.id + '">Forget</button></div>'
  ).join('');
  toggleRow.hidden = memories.length <= MEMORY_COLLAPSED_LIMIT;
  toggle.textContent = memoriesExpanded ? 'Show newest 12' : 'Show all';
  $('memory-count').textContent = memoriesExpanded
    ? memories.length + ' memories shown'
    : 'newest ' + visible.length + ' of ' + memories.length;
}

async function forget(id){
  try { await fetch('/api/memories/' + id, {method: 'DELETE'}); renderMemories((await getJSON('/api/memories')).memories); setStatus('memory', 'forgotten id ' + id + ' · ' + hm(), 'ok'); }
  catch (e) { setStatus('memory', 'forget failed: ' + e.message, 'err'); }
}
async function addMemory(){
  const el = $('memory-text'); const text = el.value.trim(); if (!text) return;
  try { await sendJSON('/api/memories', 'POST', {text}); el.value = ''; renderMemories((await getJSON('/api/memories')).memories); setStatus('memory', 'remembered · ' + hm(), 'ok'); }
  catch (e) { setStatus('memory', 'add failed: ' + e.message, 'err'); }
}

function describeLoopSchedule(loop){
  if (loop.kind !== 'scheduled' || !loop.schedule) return '';
  const s = loop.schedule;
  let text = '';
  if (s.at) text = (s.days && s.days.length ? s.days.join('/') : 'daily') + ' at ' + s.at;
  else if (s.every_seconds) {
    const m = Math.round(s.every_seconds / 60);
    text = 'every ' + (m % 60 === 0 ? (m / 60) + 'h' : m + 'm');
  }
  else if (s.once_at) text = 'once at ' + s.once_at.replace('T', ' ').slice(0, 16);
  if (loop.enabled && loop.next_run_at) {
    const mins = Math.max(0, Math.round((new Date(loop.next_run_at) - Date.now()) / 60000));
    text += ' · next in ' + (mins >= 90 ? Math.round(mins / 60) + 'h' : mins + 'm');
  }
  if (!loop.enabled && s.once_at) text += ' · fired';
  return text;
}

function renderControlLoops(loops){
  const box = $('control-loop-list');
  if (!loops.length) { box.innerHTML = '<div class="empty-message">No control loops.</div>'; return; }
  box.innerHTML = loops.map(loop => {
    const state = loop.enabled ? 'running' : 'paused';
    const sched = describeLoopSchedule(loop);
    const health = loop.last_error ? '<div class="entry-sub offline">' + esc(loop.last_error) + '</div>'
      : '<div class="entry-sub ' + (loop.has_baseline ? 'online' : '') + '">' +
        (loop.has_baseline ? 'baseline ready' : 'awaiting baseline') +
        (loop.last_checked_at ? ' · checked ' + esc(new Date(loop.last_checked_at).toLocaleString()) : '') + '</div>';
    return '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(loop.name) +
      ' <span class="entry-sub">[' + loop.id + '] ' + state + ' · ' + loop.interval_seconds + 's' +
      (sched ? ' · ' + esc(sched) : '') + '</span></div>' +
      '<div class="entry-sub">' + esc(loop.targets.join(', ')) + '</div>' +
      '<div class="entry-sub" style="white-space:pre-wrap;">trigger: ' + esc(loop.trigger_description) + '</div>' + health +
      '</div><div><button class="setting-button small" data-loop-toggle="' + loop.id + '" data-loop-enabled="' + loop.enabled + '">' +
      (loop.enabled ? 'Pause' : 'Resume') + '</button> <button class="setting-button small" data-loop-delete="' + loop.id + '">Delete</button></div></div>';
  }).join('');
}

function renderControlNotifications(notifications){
  const box = $('control-notification-list');
  if (!notifications.length) { box.innerHTML = '<div class="empty-message">No control-loop notifications.</div>'; return; }
  box.innerHTML = notifications.map(n =>
    '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(n.loop_name) +
    ' <span class="entry-sub">' + esc(new Date(n.created_at).toLocaleString()) + '</span></div>' +
    '<div style="white-space:pre-wrap;">' + esc(n.response) + '</div>' +
    '<div class="entry-sub" style="white-space:pre-wrap;">' + esc(n.summary) + '</div>' +
    (n.error ? '<div class="entry-sub offline">' + esc(n.error) + '</div>' : '') +
    '</div><button class="setting-button small" data-notification-delete="' + n.id + '">Dismiss</button></div>'
  ).join('');
}

async function refreshControlData(){
  const [loops, notifications] = await Promise.all([
    getJSON('/api/control-loops'), getJSON('/api/control-loop-notifications'),
  ]);
  renderControlLoops(loops.control_loops);
  renderControlNotifications(notifications.notifications);
}

function loopSchedulePayload(){
  const mode = $('loop-schedule-mode').value;
  if (mode === 'at') {
    const schedule = { at: $('loop-schedule-at').value };
    const days = $('loop-schedule-days').value.trim();
    if (days) schedule.days = days.split(',').map(d => d.trim().toLowerCase()).filter(Boolean);
    return schedule;
  }
  if (mode === 'every') return { every_seconds: Number($('loop-schedule-every').value) * 60 };
  return { once_at: $('loop-schedule-once').value };
}

function updateLoopScheduleVisibility(){
  const scheduled = $('loop-kind').value === 'scheduled';
  const mode = $('loop-schedule-mode').value;
  $('loop-schedule-mode').hidden = !scheduled;
  $('loop-schedule-at').hidden = !(scheduled && mode === 'at');
  $('loop-schedule-days').hidden = !(scheduled && mode === 'at');
  $('loop-schedule-every').hidden = !(scheduled && mode === 'every');
  $('loop-schedule-once').hidden = !(scheduled && mode === 'once');
}

async function addControlLoop(){
  const name = $('control-loop-name').value.trim();
  const targets = $('control-loop-targets').value.split(/[\n,]+/).map(v => v.trim()).filter(Boolean);
  const trigger = $('control-loop-trigger').value.trim();
  const interval = Number($('control-loop-interval').value);
  const kind = $('loop-kind').value;
  if (!name || (kind !== 'scheduled' && !targets.length) || !trigger) {
    setStatus('control-loop', 'name, targets, and trigger are required', 'err'); return;
  }
  if (!Number.isFinite(interval) || interval < 5 || interval > 86400) {
    setStatus('control-loop', 'interval must be 5–86400 seconds', 'err'); return;
  }
  try {
    const body = { name, targets, trigger_description: trigger, interval_seconds: interval };
    if (kind === 'scheduled') body.schedule = loopSchedulePayload();
    await sendJSON('/api/control-loops', 'POST', body);
    $('control-loop-name').value = ''; $('control-loop-targets').value = ''; $('control-loop-trigger').value = '';
    $('loop-kind').value = 'change'; $('loop-schedule-mode').value = 'at';
    $('loop-schedule-at').value = ''; $('loop-schedule-days').value = '';
    $('loop-schedule-every').value = ''; $('loop-schedule-once').value = '';
    updateLoopScheduleVisibility();
    await refreshControlData();
    setStatus('control-loop', 'created · first read will establish baseline', 'ok');
  } catch (e) { setStatus('control-loop', 'create failed: ' + e.message, 'err'); }
}

async function toggleControlLoop(id, enabled){
  try {
    await sendJSON('/api/control-loops/' + id, 'PUT', {enabled: !enabled});
    await refreshControlData();
    setStatus('control-loop', (!enabled ? 'resumed' : 'paused') + ' loop ' + id, 'ok');
  } catch (e) { setStatus('control-loop', 'update failed: ' + e.message, 'err'); }
}

async function deleteControlLoop(id){
  if (!confirm('Delete control loop ' + id + '?')) return;
  try { await sendJSON('/api/control-loops/' + id, 'DELETE'); await refreshControlData(); setStatus('control-loop', 'deleted loop ' + id, 'ok'); }
  catch (e) { setStatus('control-loop', 'delete failed: ' + e.message, 'err'); }
}

async function deleteControlNotification(id){
  try { await sendJSON('/api/control-loop-notifications/' + id, 'DELETE'); await refreshControlData(); }
  catch (e) { setStatus('control-loop', 'dismiss failed: ' + e.message, 'err'); }
}

function setEntityState(state){
  const valid = ['ready', 'listening', 'thinking', 'speaking', 'offline'];
  const next = valid.includes(state) ? state : 'ready';
  $('richard-entity').dataset.state = next;
  $('entity-state').textContent = next;
}

function setLatestReply(text, kind){
  const reply = $('latest-reply');
  reply.textContent = text || 'Ready when you are.';
  reply.classList.toggle('streaming', kind === 'streaming');
  reply.classList.toggle('err', kind === 'error');
}

function setConnected(ok){
  const el = $('connection-status'); el.classList.toggle('disconnected', !ok);
  $('conn-text').textContent = ok ? 'online' : 'offline';
  if (!ok) setEntityState('offline');
  else if ($('richard-entity').dataset.state === 'offline') setEntityState('ready');
}
function applyStatus(s){
  setConnected(true);
  $('rt-version').textContent = s.version;
  $('rt-sats').textContent = s.satellites.length;
  $('rt-loops').textContent = s.control_loops == null ? '—' : s.control_loops;
  $('foot-version').textContent = s.version;
  $('foot-sats').textContent = s.satellites.length;
}

async function refreshAll(){
  try {
    const [status, cfg, memories] = await Promise.all([
      getJSON('/api/status'), getJSON('/api/config'), getJSON('/api/memories'),
    ]);
    applyStatus(status);
    applyConfig(cfg);
    renderMemories(memories.memories);
    try { await loadVoices(); } catch (e) {}
    try { await refreshCueBank(); } catch (e) {}
    try { await loadPlugins(); } catch (e) {}
    try { await syncPerceptionStream(); } catch (e) {}
    try { await refreshHomeAssistant(); } catch (e) {}
    try { await refreshControlData(); } catch (e) { setStatus('control-loop', 'unavailable: ' + e.message, 'err'); }
  } catch (e) { setConnected(false); }
}

const chatHistory = [];
function contentText(c){
  if (typeof c === 'string') return c;
  return (c || []).filter(p => p && p.type === 'text').map(p => p.text).join(' ');
}
function contentThumbs(c){
  if (typeof c === 'string') return '';
  return (c || []).filter(p => p && p.type === 'image_url' && p.image_url && /^data:image\//.test(p.image_url.url))
    .map(p => '<img class="chat-thumb" src="' + p.image_url.url + '" alt="attached picture">').join('');
}
function renderChatHistory(){
  const log = $('chat-log');
  if (!chatHistory.length) {
    log.innerHTML = '<div class="chat-empty">No messages in this session.</div>';
    return;
  }
  log.innerHTML = chatHistory.map(message =>
    '<div class="chat-msg ' + (message.role === 'user' ? 'user' : 'richard') + '">' +
      '<span class="who">' + (message.role === 'user' ? 'you&gt;' : 'richard&gt;') + '</span>' +
      '<span class="body">' + esc(contentText(message.content)) + contentThumbs(message.content) + '</span></div>'
  ).join('');
  log.scrollTop = log.scrollHeight;
}

/* ---- pictures: resized in the browser (800 px long edge ≈ 474 prompt tokens, measured) ---- */
function frameFromCanvasSource(source, srcW, srcH, maxEdge, quality){
  if (!srcW || !srcH) return null;
  const scale = Math.min(1, maxEdge / Math.max(srcW, srcH));
  const w = Math.round(srcW * scale), h = Math.round(srcH * scale);
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  c.getContext('2d').drawImage(source, 0, 0, w, h);
  return {url: c.toDataURL('image/jpeg', quality == null ? 0.85 : quality), width: w, height: h};
}
async function shrinkImage(file, maxEdge, quality){
  const bmp = await createImageBitmap(file);
  try {
    const shot = frameFromCanvasSource(bmp, bmp.width, bmp.height, maxEdge, quality);
    if (!shot) throw new Error('empty image');
    return shot;
  } finally { bmp.close(); }
}
let pendingImage = null;  // {url, width, height, name}
function showAttachChip(){
  const chip = $('chat-attach-chip');
  if (!pendingImage) { chip.hidden = true; return; }
  $('chat-attach-thumb').src = pendingImage.url;
  $('chat-attach-name').textContent = pendingImage.name + ' · ' + pendingImage.width + '×' + pendingImage.height;
  chip.hidden = false;
}
function takePendingImage(){ const img = pendingImage; pendingImage = null; showAttachChip(); return img; }
$('chat-attach').addEventListener('click', () => $('chat-image-file').click());
$('chat-attach-clear').addEventListener('click', () => { pendingImage = null; showAttachChip(); });
$('chat-image-file').addEventListener('change', async e => {
  const file = e.target.files && e.target.files[0]; e.target.value = '';
  if (!file) return;
  try { const shot = await shrinkImage(file, 800, 0.85); pendingImage = Object.assign(shot, {name: file.name || 'picture'}); showAttachChip(); setStatus('chat', '', ''); }
  catch (err) { setStatus('chat', 'picture failed: ' + err.message, 'err'); }
});

async function sendChat(){
  const input = $('chat-input'); const text = input.value.trim();
  const image = pendingImage;
  if (!text && !image) return;
  input.value = '';
  takePendingImage();
  const content = image
    ? (text ? [{type: 'text', text}, {type: 'image_url', image_url: {url: image.url}}] : [{type: 'image_url', image_url: {url: image.url}}])
    : text;
  chatHistory.push({role: 'user', content}); renderChatHistory();
  if (rt && rt.ws.readyState === 1) {
    rtInterruptResponse();
    const realtimeContent = [];
    if (text) realtimeContent.push({type: 'input_text', text});
    if (image) realtimeContent.push({type: 'input_image', image_url: image.url});
    rtSend({type: 'conversation.item.create', item: {type: 'message', role: 'user', content: realtimeContent}});
    rtSend({type: 'response.create'});
    setEntityState('thinking'); setLatestReply('', 'streaming'); setStatus('chat', 'thinking…', '');
    return;
  }
  $('chat-send').disabled = true; $('chat-mic').disabled = true;
  setEntityState('thinking'); setLatestReply('', 'streaming'); setStatus('chat', 'thinking…', '');
  let reply = '';
  try {
    const r = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({messages: chatHistory})});
    if (!r.ok || !r.body) throw new Error('HTTP ' + r.status + (r.status === 400 ? ': ' + await r.text() : ''));
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith('data:')) continue;
        const evt = JSON.parse(line.slice(5).trim());
        if (evt.delta) { reply += evt.delta; setLatestReply(reply, 'streaming'); }
        else if (evt.error) { reply = evt.error; setLatestReply(reply, 'error'); }
      }
    }
    chatHistory.push({role: 'assistant', content: reply});
    renderChatHistory();
    setLatestReply(reply);
    setStatus('chat', '', '');
  } catch (e) {
    setLatestReply(reply || ('Chat failed: ' + e.message), 'error');
    setStatus('chat', 'chat failed: ' + e.message, 'err');
  } finally {
    setEntityState('ready'); $('chat-send').disabled = false; $('chat-mic').disabled = false; input.focus();
  }
}

/* ---- realtime hands-free voice mode (docs/realtime-api.md) ---- */
const RT_WORKLET = `
class Pcm16Capture extends AudioWorkletProcessor {
  constructor(){ super(); this.buf = []; this.len = 0; }
  process(inputs){
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    const ratio = sampleRate / 16000;          // decimate to 16 kHz
    for (let i = 0; i < ch.length; i += ratio){
      const s = Math.max(-1, Math.min(1, ch[Math.floor(i)]));
      this.buf.push(s < 0 ? s * 0x8000 : s * 0x7fff);
    }
    while (this.buf.length >= 512){            // 512-sample frames = server framing
      const frame = new Int16Array(this.buf.splice(0, 512));
      this.port.postMessage(frame.buffer, [frame.buffer]);
    }
    return true;
  }
}
registerProcessor('pcm16-capture', Pcm16Capture);
`;

let preparedCueBank = {fingerprint: null, clips: []};
let cueBankRequest = 0;
function decodePreparedPcm(audio){
  const pcm = rtPcmFromB64(audio); const decoded = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) decoded[i] = pcm[i] / 32768;
  return decoded;
}
async function refreshCueBank(){
  const request = ++cueBankRequest;
  let next = {fingerprint: null, clips: []};
  try {
    const data = await getJSON('/api/realtime/cues', {cache: 'no-store'});
    next = {fingerprint: data.fingerprint || null, clips: (data.clips || []).map(clip => ({
      id: clip.id, phase: clip.phase, sampleRate: clip.sample_rate, decoded: decodePreparedPcm(clip.audio),
    }))};
  } catch (_) {}
  if (request !== cueBankRequest) return preparedCueBank;
  preparedCueBank = next;
  if (rt && rt.presence) rt.presence.setBank(next);
  return next;
}

function makeCameraVideo(stream){
  const video = document.createElement('video');
  video.muted = true; video.playsInline = true; video.autoplay = true; video.hidden = true;
  video.srcObject = stream; document.body.appendChild(video);
  return video;
}
const sharedCamera = new SharedCamera({
  getUserMedia: constraints => navigator.mediaDevices.getUserMedia(constraints),
  makeVideo: makeCameraVideo,
});
function captureSharedFrame(maxEdge, quality){
  const media = sharedCamera.media(); const video = media && media.video;
  if (!video || !video.videoWidth || !media.track || media.track.readyState === 'ended') return null;
  return frameFromCanvasSource(video, video.videoWidth, video.videoHeight, maxEdge, quality);
}
const presenceUploader = new FrameUploader({
  sourceId: pageSourceId,
  capture: () => { const shot = captureSharedFrame(800, 0.7); return shot && shot.url.split(',')[1]; },
  upload: (source, image_base64) => sendJSON('/api/perception/frame', 'POST', {source, image_base64}),
  onError: e => setStatus('perception', 'stream: ' + e.message, 'err'),
});

let rt = null;  // {ws, ctx, micStream, node, sources, outRate, userLine, replyLine, presence}
let rtStarting = null;
let rtStartGeneration = 0;

// The same tool the Reachy Conversation App offers on the robot, so this page exercises
// the robot's protocol path end to end: the brain decides to look, we take the frame.
const CAMERA_TOOL = {
  type: 'function', name: 'camera',
  description: 'Take a picture with the webcam to answer a visual question or investigate something you noticed. Use the image as evidence for your response or next action. A request to look is not automatically a request to describe the scene; describe it only when the user asks for a description. Answer specific visual questions directly. Each call captures the current moment. Start with detail low; ask for high only to read text or see small objects.',
  parameters: {type: 'object', properties: {
    question: {type: 'string', description: 'The visual question or uncertainty this picture should help resolve.'},
    detail: {type: 'string', enum: ['low', 'high'], description: 'low (default) or high for small text and details.'}
  }, required: ['question']}
};
function rtSend(o){ if (rt && rt.ws.readyState === 1) rt.ws.send(JSON.stringify(o)); }
function rtAnswerCall(call){
  if (call.error){
    rtSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: call.id, output: JSON.stringify({error: call.error})}});
    rtSend({type: 'response.create'}); return;
  }
  let detail = 'low';
  try { detail = JSON.parse(call.args || '{}').detail === 'high' ? 'high' : 'low'; } catch (_) {}
  const shot = captureSharedFrame(detail === 'high' ? 1600 : 800, 0.85);
  if (!shot){
    rtSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: call.id, output: JSON.stringify({error: 'No frame available'})}});
    rtSend({type: 'response.create'}); return;
  }
  chatHistory.push({role: 'user', content: [{type: 'image_url', image_url: {url: shot.url}}]}); renderChatHistory();
  rtSend({type: 'conversation.item.create', item: {type: 'function_call_output', call_id: call.id,
    output: JSON.stringify({image_attached: true, image_width: shot.width, image_height: shot.height})}});
  rtSend({type: 'conversation.item.create', item: {type: 'message', role: 'user', content: [{type: 'input_image', image_url: shot.url}]}});
  rtSend({type: 'response.create'});
}

function rtB64FromBuffer(buf){
  const bytes = new Uint8Array(buf); let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function rtPcmFromB64(b64){
  const bin = atob(b64); const out = new Int16Array(bin.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = (bin.charCodeAt(2*i) | (bin.charCodeAt(2*i+1) << 8)) << 16 >> 16;
  return out;
}

function rtPlayPreparedCue(clip, onEnded){
  if (!rt) return null;
  const buf = rt.ctx.createBuffer(1, clip.decoded.length, clip.sampleRate);
  buf.getChannelData(0).set(clip.decoded);
  const src = rt.ctx.createBufferSource(); const gain = rt.ctx.createGain();
  src.buffer = buf; src.connect(gain); gain.connect(rt.ctx.destination);
  src.onended = onEnded;
  src.start(rt.ctx.currentTime + 0.01);
  return {source: src, gain, stop(){
    const now = rt ? rt.ctx.currentTime : 0;
    try {
      gain.gain.cancelScheduledValues(now); gain.gain.setValueAtTime(gain.gain.value, now);
      gain.gain.linearRampToValueAtTime(0, now + 0.04); src.stop(now + 0.05);
    } catch (_) { try { src.stop(); } catch (_) {} }
  }};
}

function rtPlay(b64, responseId){
  if (!rt || !rt.presence.realAudioScheduled(responseId)) return;
  const pcm = rtPcmFromB64(b64);
  const buf = rt.ctx.createBuffer(1, pcm.length, rt.outRate);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = rt.ctx.createBufferSource();
  src.buffer = buf; src.connect(rt.ctx.destination);
  const t = Math.max(rt.ctx.currentTime + 0.05, rt.playhead);
  const entry = {source: src, responseId};
  src.onended = () => {
    if (!rt) return;
    const index = rt.sources.indexOf(entry); if (index >= 0) rt.sources.splice(index, 1);
    rt.presence.realAudioEnded(responseId);
  };
  src.start(t); rt.playhead = t + buf.duration; rt.sources.push(entry);
}

function rtFlushPlayback(state){
  const current = state || rt; if (!current) return;
  const responseIds = new Set(current.sources.map(entry => entry.responseId));
  current.sources.forEach(entry => { entry.source.onended = null; try { entry.source.stop(); } catch (_) {} });
  current.sources = []; current.playhead = 0;
  responseIds.forEach(responseId => current.presence.realAudioFlushed(responseId));
}

function rtInterruptResponse(){
  if (!rt) return;
  rt.presence.speechStarted();
  rtFlushPlayback();
  rt.pendingCall = null;
  rt.replyLine = '';
}

function rtHandle(msg){
  switch (msg.type){
    case 'session.created':
      rt.outRate = msg.session.output_audio_samplerate;
      rtSend({type: 'session.update', session: {
        tools: rt.video ? [CAMERA_TOOL] : [], source_id: pageSourceId,
        playback_ack: true, visual_context: !!rt.video,
      }});
      if (rt.video) presenceUploader.requestFresh().catch(e => setStatus('perception', 'stream: ' + e.message, 'err'));
      break;
    case 'response.activity': rt.presence.activity(msg); break;
    case 'response.function_call_arguments.done':
      if (!rt.presence.accepts(msg.response_id)) break;
      rt.pendingCall = (msg.name === 'camera' && rt.video)
        ? {id: msg.call_id, args: msg.arguments}
        : {id: msg.call_id, error: 'unknown tool ' + msg.name};
      setEntityState('thinking'); setStatus('chat', 'looking…', '');
      break;
    case 'input_audio_buffer.speech_started':
      // Barge-in: synthesis outruns playback, so the server can already be done
      // ("listening") while seconds of audio are still queued here — truncated
      // never arrives for a completed response. The client owns playback, so
      // speech onset must flush the queue locally, whatever the server state.
      rtInterruptResponse();
      setEntityState('listening'); setStatus('chat', 'listening…', ''); rt.userLine = '';
      break;
    case 'conversation.item.input_audio_transcription.delta': setStatus('chat', '“' + msg.delta + '”', ''); break;
    case 'conversation.item.input_audio_transcription.completed':
      rt.userLine = msg.transcript;
      chatHistory.push({role: 'user', content: msg.transcript}); renderChatHistory();
      break;
    case 'response.created':
      rt.presence.responseCreated(msg.response);
      setEntityState('thinking'); setStatus('chat', 'thinking…', ''); rt.replyLine = '';
      break;
    case 'response.output_text.delta': if (rt.presence.accepts(msg.response_id)) rt.replyLine += msg.delta; break;
    case 'response.audio.delta':
      if (!rt.presence.accepts(msg.response_id)) break;
      setEntityState('speaking'); setStatus('chat', 'speaking…', ''); rtPlay(msg.delta, msg.response_id);
      break;
    case 'conversation.item.truncated':
      if (!rt.presence.accepts(msg.item_id)) break;
      rtFlushPlayback(); rt.presence.responseDone({id: msg.item_id, status: 'cancelled'});
      break;
    case 'response.done':
      if (!rt.presence.responseDone(msg.response)) break;
      if (rt.replyLine){ chatHistory.push({role: 'assistant', content: rt.replyLine}); renderChatHistory(); setLatestReply(rt.replyLine); }
      if (rt.pendingCall){ const call = rt.pendingCall; rt.pendingCall = null; rtAnswerCall(call); break; }
      setEntityState('ready'); setStatus('chat', '', '');
      break;
    case 'error': rt.presence.protocolError(msg.response_id); setStatus('chat', 'voice: ' + msg.error.message, 'err'); break;
  }
}

function cleanupVoiceStartup(startup){
  if (startup.ws) {
    try { startup.ws.onclose = null; startup.ws.close(); } catch (_) {}
    startup.ws = null;
  }
  if (startup.micStream) {
    startup.micStream.getTracks().forEach(t => t.stop());
    startup.micStream = null;
  }
  if (startup.ctx) { startup.ctx.close().catch(() => {}); startup.ctx = null; }
  if (!startup.cameraReleased) {
    sharedCamera.release(startup.cameraOwner);
    startup.cameraReleased = true;
  }
}

async function startVoiceMode(){
  if (rt || rtStarting) return;
  const generation = ++rtStartGeneration;
  const startup = {generation, cameraOwner: 'voice-' + generation, ws: null, micStream: null, ctx: null, cameraReleased: false};
  const isCurrent = () => rtStarting === startup && generation === rtStartGeneration;
  rtStarting = startup; $('voice-mode').disabled = true;
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
      setStatus('chat', 'mic needs HTTPS — open the https:// address', 'err'); return;
    }
    let cfg;
    try { cfg = await getJSON('/api/config'); }
    catch (e) { setStatus('chat', 'config unavailable: ' + e.message, 'err'); return; }
    if (!isCurrent()) return;
    if (!cfg.realtime || !cfg.realtime.enabled){ setStatus('chat', 'realtime API disabled in config', 'err'); return; }
    await refreshCueBank();
    if (!isCurrent()) return;
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const tok = cfg.realtime.token ? ('?token=' + encodeURIComponent(cfg.realtime.token)) : '';
    let ws, micStream, cameraMedia, ctx, wsFailedEarly = false, preMsgs = [];
    try {
      ws = new WebSocket(scheme + '://' + location.hostname + ':' + cfg.realtime.port + '/v1/realtime' + tok);
      startup.ws = ws;
      // Buffer messages until the operational handler is attached below — the
      // server emits session.created immediately on connect, and the mic/worklet
      // awaits that follow would otherwise drop it on a listener-less socket.
      ws.onmessage = e => preMsgs.push(e.data);
      ws.onerror = () => { wsFailedEarly = true; setStatus('chat', 'voice connection failed', 'err'); };
      ws.onclose = () => { wsFailedEarly = true; setStatus('chat', 'voice connection closed — check realtime server/token', 'err'); };
      const audioSpec = {echoCancellation: true, noiseSuppression: true, channelCount: 1};
      const camera = sharedCamera.acquire(startup.cameraOwner).catch(() => null);
      micStream = await navigator.mediaDevices.getUserMedia({audio: audioSpec});
      startup.micStream = micStream;
      if (!isCurrent()) { cleanupVoiceStartup(startup); return; }
      cameraMedia = await camera;
      if (!isCurrent()) { cleanupVoiceStartup(startup); return; }
      ctx = new AudioContext();
      startup.ctx = ctx;
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([RT_WORKLET], {type: 'text/javascript'})));
    } catch (e) {
      cleanupVoiceStartup(startup);
      if (isCurrent()) setStatus('chat', 'mic blocked: ' + e.message, 'err');
      return;
    }
    if (!isCurrent() || wsFailedEarly || ws.readyState > 1) {
      cleanupVoiceStartup(startup);
      return;
    }
    const node = new AudioWorkletNode(ctx, 'pcm16-capture');
    node.port.onmessage = e => {
      if (ws.readyState === 1) ws.send(JSON.stringify({type: 'input_audio_buffer.append', audio: rtB64FromBuffer(e.data)}));
    };
    ctx.createMediaStreamSource(micStream).connect(node);
    rt = {ws, ctx, micStream, node, cameraOwner: startup.cameraOwner, playhead: 0, sources: [], outRate: 24000, userLine: '', replyLine: ''};
    rt.pendingCall = null; rt.video = cameraMedia && cameraMedia.video;
    rt.presence = new RealtimePresenceController({
      send: rtSend,
      playClip: rtPlayPreparedCue,
      stopClip: handle => { if (handle) handle.stop(); },
    });
    rt.presence.setBank(preparedCueBank);
    ws.onclose = () => stopVoiceMode();
    ws.onerror = () => setStatus('chat', 'voice connection failed', 'err');
    ws.onmessage = e => { try { rtHandle(JSON.parse(e.data)); } catch (_) {} };
    preMsgs.forEach(d => { try { rtHandle(JSON.parse(d)); } catch (_) {} });
    preMsgs = null;
    $('voice-mode').classList.add('recording');
    setEntityState('ready'); setStatus('chat', rt.video ? 'hands-free — just talk; Richard can look through the webcam' : 'hands-free — just talk', '');
  } finally {
    if (rtStarting === startup) {
      rtStarting = null;
      $('voice-mode').disabled = false;
    }
  }
}

function stopVoiceMode(){
  ++rtStartGeneration;
  if (rtStarting) {
    const startup = rtStarting; rtStarting = null;
    cleanupVoiceStartup(startup);
  }
  if (!rt) { $('voice-mode').disabled = false; return; }
  const r = rt;
  sharedCamera.release(r.cameraOwner);
  rtFlushPlayback(r); r.presence.disconnect();
  rt = null;
  try { r.ws.onclose = null; r.ws.close(); } catch (_) {}
  r.micStream.getTracks().forEach(t => t.stop());
  r.ctx.close().catch(() => {});
  $('voice-mode').classList.remove('recording');
  setEntityState('ready'); setStatus('chat', '', '');
}
/* ---- end realtime hands-free voice mode ---- */

let mediaRecorder = null, mediaChunks = [], mediaStream = null;
async function startRecording(){
  if (mediaRecorder && mediaRecorder.state === 'recording') return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    setStatus('chat', 'mic needs HTTPS — open the https:// address', 'err'); return;
  }
  try { mediaStream = await navigator.mediaDevices.getUserMedia({audio: true}); }
  catch (e) { setLatestReply('Microphone blocked: ' + e.message, 'error'); setStatus('chat', 'mic blocked: ' + e.message, 'err'); return; }
  mediaChunks = [];
  mediaRecorder = new MediaRecorder(mediaStream);
  mediaRecorder.ondataavailable = e => { if (e.data && e.data.size) mediaChunks.push(e.data); };
  mediaRecorder.onstop = onRecordingStop;
  mediaRecorder.start();
  $('chat-mic').classList.add('recording');
  setEntityState('listening');
  setStatus('chat', '● recording… release to send', '');
}
function stopRecording(){
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    setEntityState('thinking');
  }
  $('chat-mic').classList.remove('recording');
}
async function onRecordingStop(){
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
  const blob = new Blob(mediaChunks, {type: (mediaChunks[0] && mediaChunks[0].type) || 'audio/webm'});
  if (!blob.size) { setEntityState('ready'); setStatus('chat', '', ''); return; }
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let bin = ''; for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  const b64 = btoa(bin);
  setEntityState('thinking'); setStatus('chat', 'thinking…', ''); $('chat-mic').disabled = true; $('chat-send').disabled = true;
  try {
    const image = takePendingImage();
    if (image) { chatHistory.push({role: 'user', content: [{type: 'image_url', image_url: {url: image.url}}]}); renderChatHistory(); }
    const data = await sendJSON('/api/voice', 'POST', {messages: chatHistory, audio: b64});
    if (data.error) throw new Error(data.error);
    if (!data.transcript) { setLatestReply("I didn't catch that."); setEntityState('ready'); setStatus('chat', "didn't catch that", ''); return; }
    chatHistory.push({role: 'user', content: data.transcript});
    chatHistory.push({role: 'assistant', content: data.reply});
    renderChatHistory(); setLatestReply(data.reply);
    if (data.audio) {
      setEntityState('speaking'); setStatus('chat', 'speaking…', '');
      const a = new Audio(data.audio);
      a.onended = () => { setEntityState('ready'); setStatus('chat', '', ''); };
      a.play().catch(() => { setEntityState('ready'); setStatus('chat', '', ''); });
    } else { setEntityState('ready'); setStatus('chat', '', ''); }
  } catch (e) {
    setLatestReply('Voice failed: ' + e.message, 'error'); setEntityState('ready');
    setStatus('chat', 'voice failed: ' + e.message, 'err');
  }
  finally { $('chat-mic').disabled = false; $('chat-send').disabled = false; }
}

/* ---- ambient perception: this device's camera as a frame source, and the page ---- */
const perception = {video: null, timer: null, enabled: false, fps: 2, poll: null, starting: null, generation: 0};
async function startPerceptionStream(){
  if (perception.timer || perception.starting || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return perception.starting;
  const generation = ++perception.generation;
  perception.starting = sharedCamera.acquire('ambient');
  let media;
  try {
    media = await perception.starting;
  } catch (e) {
    if (generation === perception.generation) setStatus('perception', 'camera blocked: ' + e.message, 'err');
    return;
  } finally {
    if (generation === perception.generation) perception.starting = null;
  }
  if (generation !== perception.generation || !media) return;
  perception.video = media.video;
  $('perception-indicator').hidden = false;
  presenceUploader.request().catch(e => setStatus('perception', 'stream: ' + e.message, 'err'));
  perception.timer = setInterval(() => {
    presenceUploader.request().catch(e => setStatus('perception', 'stream: ' + e.message, 'err'));
  }, Math.max(100, 1000 / perception.fps));
}
function stopPerceptionStream(){
  ++perception.generation; perception.starting = null;
  if (perception.timer) { clearInterval(perception.timer); perception.timer = null; }
  perception.video = null;
  sharedCamera.release('ambient');
  $('perception-indicator').hidden = true;
}
function capturePerceptionFrame(maxEdge){
  return captureSharedFrame(maxEdge, 0.85);
}
async function syncPerceptionStream(){
  let status;
  try { status = await getJSON('/api/perception/status'); } catch (_) { return; }
  perception.enabled = !!status.enabled;
  if (perception.enabled && document.visibilityState === 'visible') startPerceptionStream();
  else stopPerceptionStream();
  return status;
}
document.addEventListener('visibilitychange', () => { syncPerceptionStream(); });
window.addEventListener('pagehide', () => {
  presenceUploader.cancelPendingFresh(); stopVoiceMode(); stopPerceptionStream();
});

function renderPerceptionEvents(rows){
  const box = $('perception-events');
  if (!rows.length) { box.innerHTML = '<div class="empty-message">No events yet.</div>'; return; }
  box.innerHTML = rows.slice().reverse().map(r =>
    '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(r.kind.replace(/_/g, ' ')) + (r.subject ? ' · ' + esc(r.subject) : '') + '</div>' +
    '<div class="entry-sub">' + esc((r.at || '').replace('T', ' ').slice(0, 19)) + ' · ' + esc(r.source) + '</div></div></div>').join('');
}
function renderGallery(people){
  const box = $('perception-gallery');
  if (!people.length) { box.innerHTML = '<div class="empty-message">Nobody enrolled.</div>'; return; }
  box.innerHTML = people.map(p => '<div class="entry"><div class="entry-main"><div class="entry-name">' + esc(p.name) + '</div><div class="entry-sub">' + p.samples + ' snapshots · ' + esc(p.enrolled_at || '') + '</div></div>' +
    '<button class="setting-button small" data-gallery-delete="' + esc(p.name) + '">Delete</button></div>').join('');
}
async function loadPerception(){
  try {
    const cfg = await getJSON('/api/perception/config');
    for (const key of ['quiet_hours', 'sensitivity', 'cooldown_s', 'stream_fps']) $('perception.' + key).value = cfg[key];
    $('perception.identity_enabled').checked = !!cfg.identity_enabled;
    $('perception.keep_thumbnails').checked = !!cfg.keep_thumbnails;
    const plugins = (await getJSON('/api/plugins')).plugins;
    const row = plugins.find(p => p.name === 'perception');
    $('perception.plugin_enabled').checked = !!(row && row.configured);
    const status = await syncPerceptionStream();
    if (status && status.enabled) {
      $('perception-sources').textContent = status.sources.map(s => s.id + (s.stale ? ' (stale)' : '') + ' · ' + s.frames + ' frames').join(', ') || 'no source yet';
      $('perception-presence').textContent = status.presence.length ? status.presence.map(p => p.subject + ' since ' + Math.round(p.since)).join(', ') : 'nobody in view';
      renderGallery(status.gallery || []);
      const ev = await getJSON('/api/perception/events');
      renderPerceptionEvents(ev.events);
      const img = $('perception-latest'); img.src = '/api/perception/latest.jpg?t=' + Date.now(); img.hidden = false;
    } else {
      $('perception-sources').textContent = 'plugin disabled';
      $('perception-latest').hidden = true;
    }
  } catch (e) { setStatus('perception', 'unavailable: ' + e.message, 'err'); }
}
async function savePerception(){
  const patch = {
    quiet_hours: $('perception.quiet_hours').value.trim(), sensitivity: Number($('perception.sensitivity').value),
    cooldown_s: Number($('perception.cooldown_s').value), stream_fps: Number($('perception.stream_fps').value),
    identity_enabled: $('perception.identity_enabled').checked, keep_thumbnails: $('perception.keep_thumbnails').checked,
  };
  try {
    await sendJSON('/api/perception/config', 'PUT', patch);
    const wantPlugin = $('perception.plugin_enabled').checked;
    await sendJSON('/api/plugins', 'PUT', {name: 'perception', enabled: wantPlugin});
    setStatus('perception', 'saved · restart to apply · ' + hm(), 'ok');
  } catch (e) { setStatus('perception', 'save failed: ' + e.message, 'err'); }
}
async function enrolFace(){
  const name = $('perception-enrol-name').value.trim();
  if (!name) { setStatus('perception-gallery', 'a name is required', 'err'); return; }
  if (!perception.video && !(rt && rt.video)) { await startPerceptionStream(); await new Promise(r => setTimeout(r, 800)); }
  const shots = [];
  for (let i = 0; i < 3; i++) {
    const shot = capturePerceptionFrame(800);
    if (shot) shots.push(shot.url.split(',')[1]);
    await new Promise(r => setTimeout(r, 700));
  }
  if (shots.length < 3) { setStatus('perception-gallery', 'camera not ready', 'err'); return; }
  try {
    const out = await sendJSON('/api/perception/gallery', 'POST', {name, images_base64: shots});
    setStatus('perception-gallery', out.name + ' enrolled (' + out.samples + ' snapshots) · ' + hm(), 'ok');
    renderGallery((await getJSON('/api/perception/gallery')).people);
  } catch (e) { setStatus('perception-gallery', 'enrol failed: ' + e.message, 'err'); }
}
$('perception-save').addEventListener('click', savePerception);
$('perception-restart').addEventListener('click', () => sendJSON('/api/restart', 'POST').catch(() => {}));
$('perception-enrol').addEventListener('click', enrolFace);
$('perception-gallery').addEventListener('click', async e => {
  const b = e.target.closest('[data-gallery-delete]'); if (!b) return;
  try { await fetch('/api/perception/gallery/' + encodeURIComponent(b.dataset.galleryDelete), {method: 'DELETE'}); renderGallery((await getJSON('/api/perception/gallery')).people); }
  catch (err) { setStatus('perception-gallery', 'delete failed: ' + err.message, 'err'); }
});
document.querySelector('[data-drawer-open="perception"]').addEventListener('click', () => {
  loadPerception();
  if (perception.poll) clearInterval(perception.poll);
  perception.poll = setInterval(() => { if (activeDrawerPage === 'perception') loadPerception(); else { clearInterval(perception.poll); perception.poll = null; } }, 3000);
});

let activeDrawerPage = 'menu';

function mountDrawerPanels(){
  document.querySelectorAll('[data-panel-content]').forEach(mount => {
    const ids = mount.dataset.panelContent.split(/\s+/).filter(Boolean);
    for (const id of ids) {
      const content = $(id);
      const panel = content && content.closest('.terminal-section');
      if (panel) mount.appendChild(panel);
    }
  });
}

function showDrawerPage(name){
  const next = document.querySelector('[data-drawer-page="' + name + '"]');
  if (!next) return;
  document.querySelectorAll('[data-drawer-page]').forEach(page => {
    const active = page === next;
    page.hidden = !active;
    page.classList.toggle('is-active', active);
  });
  activeDrawerPage = name;
  const title = next.querySelector('.drawer-title');
  if (title) title.focus();
}

function openDrawer(){
  const drawer = $('richard-drawer');
  drawer.classList.add('is-open');
  drawer.setAttribute('aria-hidden', 'false');
  $('menu-toggle').setAttribute('aria-expanded', 'true');
  $('menu-toggle').setAttribute('aria-label', 'Close menu');
  $('drawer-backdrop').hidden = false;
  document.body.classList.add('menu-open');
  showDrawerPage(activeDrawerPage);
}

function closeDrawer(){
  const drawer = $('richard-drawer');
  drawer.classList.remove('is-open');
  drawer.setAttribute('aria-hidden', 'true');
  $('menu-toggle').setAttribute('aria-expanded', 'false');
  $('menu-toggle').setAttribute('aria-label', 'Open menu');
  $('drawer-backdrop').hidden = true;
  document.body.classList.remove('menu-open');
  $('menu-toggle').focus();
}

function trapDrawerFocus(e){
  if (e.key !== 'Tab' || !$('richard-drawer').classList.contains('is-open')) return;
  const active = document.querySelector('[data-drawer-page]:not([hidden])');
  if (!active) return;
  const focusable = active.querySelectorAll('button, input, select, textarea, [tabindex]:not([tabindex="-1"])');
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
}

// --- wiring ---
mountDrawerPanels();
$('menu-toggle').addEventListener('click', () => {
  if ($('richard-drawer').classList.contains('is-open')) closeDrawer();
  else openDrawer();
});
$('drawer-backdrop').addEventListener('click', closeDrawer);
document.querySelectorAll('[data-drawer-open]').forEach(button =>
  button.addEventListener('click', () => showDrawerPage(button.dataset.drawerOpen)));
document.querySelectorAll('[data-drawer-back]').forEach(button =>
  button.addEventListener('click', () => showDrawerPage(button.dataset.drawerBack)));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && $('richard-drawer').classList.contains('is-open')) closeDrawer();
  trapDrawerFocus(e);
});
document.querySelectorAll('[data-save]').forEach(b => b.addEventListener('click', () => saveSection(b.dataset.save)));
document.querySelectorAll('.terminal-header-line[data-target]').forEach(h => h.addEventListener('click', () => {
  const c = $(h.dataset.target); if (!c) return;
  c.classList.toggle('collapsed');
  h.querySelector('.window-control').classList.toggle('is-open', !c.classList.contains('collapsed'));
}));
for (const f of FIELDS) {
  const el = $(f.id); if (!el) continue;
  const ev = (f.t === 'bool' || f.t === 'sel') ? 'change' : 'input';
  el.addEventListener(ev, () => {
    if (f.t === 'dial') { const o = $(f.id + '.val'); if (o) o.textContent = el.value; }
    const e = validate(f); el.classList.toggle('invalid', !!e);
    reflectDirty(f.sec);
  });
}
$('home-assistant-test').addEventListener('click', () => refreshHomeAssistant());
$('home-assistant-restart').addEventListener('click', rebootServe);
$('voice.tts_engine').addEventListener('change', () => { reflectRemoteOnly(); loadVoices(); });
reflectRemoteOnly();  // initial state before /api/config answers
$('voice-sample-upload').addEventListener('click', uploadVoiceSample);
$('voice-sample-restart').addEventListener('click', rebootServe);
$('voice-list').addEventListener('click', e => { const b = e.target.closest('[data-use-voice]'); if (b) useVoice(b.dataset.useVoice); });
$('plugins-refresh').addEventListener('click', loadPlugins);
$('plugins-restart').addEventListener('click', rebootServe);
$('plugins-list').addEventListener('click', e => { const b = e.target.closest('[data-plugin-toggle]'); if (b) togglePlugin(b.dataset.pluginToggle, b.dataset.pluginEnabled !== '1'); });
$('home-assistant-entity-filter').addEventListener('input', filterHomeAssistantEntities);
$('memory-add').addEventListener('click', addMemory);
$('memory-text').addEventListener('keydown', e => { if (e.key === 'Enter') addMemory(); });
$('memory-list').addEventListener('click', e => { const b = e.target.closest('[data-forget]'); if (b) forget(b.dataset.forget); });
$('memory-toggle').addEventListener('click', () => { memoriesExpanded = !memoriesExpanded; renderMemories(memoryCache); });
$('control-loop-add').addEventListener('click', addControlLoop);
$('control-loop-refresh').addEventListener('click', () => refreshControlData().catch(e => setStatus('control-loop', e.message, 'err')));
$('loop-kind').addEventListener('change', updateLoopScheduleVisibility);
$('loop-schedule-mode').addEventListener('change', updateLoopScheduleVisibility);
updateLoopScheduleVisibility();
$('control-loop-list').addEventListener('click', e => {
  const toggle = e.target.closest('[data-loop-toggle]');
  if (toggle) toggleControlLoop(toggle.dataset.loopToggle, toggle.dataset.loopEnabled === 'true');
  const remove = e.target.closest('[data-loop-delete]');
  if (remove) deleteControlLoop(remove.dataset.loopDelete);
});
$('control-notification-list').addEventListener('click', e => {
  const remove = e.target.closest('[data-notification-delete]');
  if (remove) deleteControlNotification(remove.dataset.notificationDelete);
});
$('serve-reboot').addEventListener('click', rebootServe);
$('chat-send').addEventListener('click', sendChat);
$('chat-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendChat(); });
(() => {
  const mic = $('chat-mic');
  mic.addEventListener('mousedown', startRecording);
  mic.addEventListener('mouseup', stopRecording);
  mic.addEventListener('mouseleave', stopRecording);
  mic.addEventListener('touchstart', e => { e.preventDefault(); startRecording(); });
  mic.addEventListener('touchend', e => { e.preventDefault(); stopRecording(); });
})();
$('voice-mode').addEventListener('click', () => { rt ? stopVoiceMode() : startVoiceMode(); });

refreshAll();
setInterval(() => { getJSON('/api/status').then(applyStatus).catch(() => setConnected(false)); }, 5000);
setInterval(() => { refreshControlData().catch(() => {}); }, 10000);
setInterval(() => { syncPerceptionStream().catch(() => {}); }, 30000);
</script>
</body>
</html>
"""
