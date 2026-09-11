const test = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const vm = require('node:vm');

function renderedSpaScript() {
  const html = execFileSync('.venv/bin/python', ['-c', 'from richard.web.static import SPA_HTML; print(SPA_HTML)'], {
    cwd: process.cwd(),
    env: {...process.env, PYTHONPATH: 'src:.'},
    encoding: 'utf8',
  });
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
    .map(match => match[1])
    .filter(Boolean);
  assert.equal(scripts.length, 1, 'the rendered page has one inline SPA handler');
  return scripts[0];
}

class ClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach(name => this.values.add(name)); }
  remove(...names) { names.forEach(name => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.values.has(name) : !!force;
    if (enabled) this.values.add(name); else this.values.delete(name);
    return enabled;
  }
}

class Element {
  constructor(tag = 'div', id = '') {
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.value = '';
    this.checked = false;
    this.hidden = false;
    this.disabled = false;
    this.dataset = {};
    this.classList = new ClassList();
    this.listeners = new Map();
    this.children = [];
    this.files = [];
    this.style = {};
    this.videoWidth = tag === 'video' ? 1280 : 0;
    this.videoHeight = tag === 'video' ? 720 : 0;
    this.width = 0;
    this.height = 0;
    this.textContent = '';
    this.innerHTML = '';
  }
  addEventListener(type, fn) {
    const callbacks = this.listeners.get(type) || [];
    callbacks.push(fn);
    this.listeners.set(type, callbacks);
  }
  dispatchEvent(event) {
    event.target ||= this;
    for (const fn of this.listeners.get(event.type) || []) fn(event);
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  remove() { this.removed = true; }
  focus() {}
  setAttribute(name, value) { this[name] = String(value); }
  getAttribute(name) { return this[name]; }
  querySelector() { return new Element(); }
  querySelectorAll() { return []; }
  closest() { return null; }
  play() { return Promise.resolve(); }
  getContext() {
    return {drawImage() {}};
  }
  toDataURL() { return 'data:image/jpeg;base64,anBlZw=='; }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function makeTrack(kind) {
  return {kind, readyState: 'live', stopped: 0, stop() { this.readyState = 'ended'; this.stopped++; }};
}

class FakeStream {
  constructor(tracks = []) { this.tracks = tracks; }
  getTracks() { return this.tracks; }
  getVideoTracks() { return this.tracks.filter(track => track.kind === 'video'); }
  getAudioTracks() { return this.tracks.filter(track => track.kind === 'audio'); }
}

class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; }
  emit(message) { this.onmessage({data: JSON.stringify(message)}); }
}

class FakeAudioContext {
  static instances = [];
  constructor() {
    this.currentTime = 0;
    this.destination = {};
    this.sources = [];
    this.audioWorklet = {addModule: async () => {}};
    FakeAudioContext.instances.push(this);
  }
  createBuffer(_channels, length, rate) {
    const data = new Float32Array(length);
    return {duration: length / rate, sampleRate: rate, getChannelData: () => data};
  }
  createBufferSource() {
    const source = {
      connect() { return this; },
      start(at) { this.startedAt = at; },
      stop() { this.stopped = true; },
      onended: null,
    };
    this.sources.push(source);
    return source;
  }
  createGain() {
    return {
      gain: {value: 1, cancelScheduledValues() {}, setValueAtTime() {}, linearRampToValueAtTime() {}},
      connect() { return this; },
    };
  }
  createMediaStreamSource() { return {connect() {}}; }
  close() { return Promise.resolve(); }
}

class FakeWorkletNode { constructor() { this.port = {}; } }

function response(body, status = 200) {
  const text = JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    body: null,
    json: async () => body,
    text: async () => text,
  };
}

function baseConfig() {
  return {
    llm_endpoint: 'http://brain', llm_model: 'model', prompt_default: '',
    personality: {}, voice: {}, home_assistant: {enabled: false, host: '', port: 8123, token_configured: false},
    satellite: {enabled: false, host: '', port: 8770}, web: {enabled: true, host: '', port: 8771},
    realtime: {enabled: true, port: 8766, token: ''},
  };
}

function createHarness(options = {}) {
  FakeWebSocket.instances = [];
  FakeAudioContext.instances = [];
  const elements = new Map();
  const body = new Element('body', 'body');
  const documentListeners = new Map();
  const document = {
    body,
    visibilityState: 'visible',
    activeElement: null,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element('div', id));
      return elements.get(id);
    },
    createElement(tag) { return new Element(tag); },
    querySelector(selector) {
      if (selector.includes('data-drawer-open="perception"')) return this.getElementById('perception-open');
      return new Element();
    },
    querySelectorAll() { return []; },
    addEventListener(type, fn) {
      const callbacks = documentListeners.get(type) || [];
      callbacks.push(fn); documentListeners.set(type, callbacks);
    },
  };
  document.getElementById('richard-entity').dataset.state = 'ready';
  document.getElementById('chat-input').value = '';
  const cameraPending = options.cameraPending || deferred();
  const cameraTrack = makeTrack('video');
  const micTracks = [];
  const mediaCalls = [];
  const framePosts = [];
  const frameReplies = [];
  const fetchCalls = [];
  const fetch = async (url, init = {}) => {
    fetchCalls.push([url, init]);
    if (url === '/api/config') return response(baseConfig());
    if (url === '/api/realtime/cues') return response({fingerprint: 'voice-a', language: 'en', clips: []});
    if (url === '/api/status') return response({version: 'test', satellites: [], control_loops: 0, unread_notifications: 0});
    if (url === '/api/memories') return response({memories: []});
    if (url === '/api/plugins') return response({plugins: [{name: 'perception', configured: true}]});
    if (url === '/api/home-assistant') return response({connected: false, entities: [], domains: {}, enabled: false});
    if (url === '/api/control-loops') return response({control_loops: []});
    if (url === '/api/control-loop-notifications') return response({notifications: []});
    if (url === '/api/perception/status') return response({enabled: options.perceptionEnabled !== false, sources: [], presence: [], gallery: []});
    if (url === '/api/perception/frame') {
      framePosts.push(JSON.parse(init.body));
      return frameReplies.length ? frameReplies.shift().promise : response({ok: true});
    }
    if (url === '/api/chat') throw new Error('HTTP chat must not run while realtime is active');
    return response({});
  };
  const navigator = {mediaDevices: {getUserMedia: constraints => {
    mediaCalls.push(constraints);
    if (constraints.video) return cameraPending.promise;
    const track = makeTrack('audio'); micTracks.push(track);
    return Promise.resolve(new FakeStream([track]));
  }}};
  const intervals = [];
  const windowListeners = new Map();
  const sandbox = {
    console,
    document,
    navigator,
    location: {protocol: 'https:', hostname: 'localhost'},
    crypto: {randomUUID: () => '123e4567-e89b-12d3-a456-426614174000'},
    fetch,
    WebSocket: FakeWebSocket,
    AudioContext: FakeAudioContext,
    AudioWorkletNode: FakeWorkletNode,
    MediaStream: FakeStream,
    Blob,
    TextDecoder,
    URL: {createObjectURL: () => 'blob:test'},
    Event: class { constructor(type) { this.type = type; } },
    FileReader: class {},
    MediaRecorder: class {},
    createImageBitmap: async () => {},
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    confirm: () => true,
    setTimeout,
    clearTimeout,
    setInterval: fn => { intervals.push(fn); return intervals.length; },
    clearInterval() {},
    RichardRealtimeClient: require('../../src/richard/web/realtime_client.js'),
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  sandbox.addEventListener = (type, fn) => {
    const callbacks = windowListeners.get(type) || [];
    callbacks.push(fn); windowListeners.set(type, callbacks);
  };
  vm.createContext(sandbox);
  const exports = `\n;globalThis.__spa = {
    startVoiceMode, stopVoiceMode, startPerceptionStream, stopPerceptionStream,
    sendChat, rtHandle, getRt: () => rt, getPerception: () => perception,
    sharedCamera, presenceUploader, setPendingImage: value => { pendingImage = value; }
  };`;
  vm.runInContext(renderedSpaScript() + exports, sandbox, {filename: 'rendered-spa.js'});
  return {
    sandbox, document, elements, cameraPending, cameraTrack, micTracks, mediaCalls,
    framePosts, frameReplies, fetchCalls, intervals,
    resolveCamera() { cameraPending.resolve(new FakeStream([cameraTrack])); },
    async settle() { await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve)); },
  };
}

test('actual ambient and voice handlers share one camera and negotiate the page source', async () => {
  const h = createHarness();
  const ambient = h.sandbox.__spa.startPerceptionStream();
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle();
  assert.equal(h.mediaCalls.filter(call => call.video).length, 1);
  h.resolveCamera();
  await Promise.all([ambient, voice]);
  const ws = FakeWebSocket.instances[0];
  ws.emit({type: 'session.created', session: {output_audio_samplerate: 24000}});
  await h.settle();

  const update = ws.sent.find(message => message.type === 'session.update');
  assert.equal(update.session.source_id, 'browser-123e4567-e89b-12d3-a456-426614174000');
  assert.equal(update.session.playback_ack, true);
  assert.equal(update.session.visual_context, true);
  assert.ok(update.session.tools.some(tool => tool.name === 'camera'));
  assert.equal(h.framePosts[0].source, update.session.source_id);

  h.sandbox.__spa.stopVoiceMode();
  assert.equal(h.cameraTrack.stopped, 0);
  h.sandbox.__spa.stopPerceptionStream();
  assert.equal(h.cameraTrack.stopped, 1);
});

test('voice startup rechecks the prepared cue bank without HTTP cache reuse', async () => {
  const h = createHarness();
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle();
  h.resolveCamera();
  await voice;

  const cueRequest = h.fetchCalls.find(([url]) => url === '/api/realtime/cues');
  assert.equal(cueRequest[1].cache, 'no-store');
});

test('actual session handler queues a fresh frame behind an existing upload', async () => {
  const first = deferred();
  const h = createHarness();
  h.frameReplies.push(first);
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle(); h.resolveCamera(); await voice;
  const firstUpload = h.sandbox.__spa.presenceUploader.request();
  await h.settle();
  assert.equal(h.framePosts.length, 1);
  FakeWebSocket.instances[0].emit({type: 'session.created', session: {output_audio_samplerate: 24000}});
  await h.settle();
  assert.equal(h.framePosts.length, 1);
  first.resolve(response({ok: true}));
  await firstUpload; await h.settle();
  assert.equal(h.framePosts.length, 2);
});

test('actual response handler acknowledges playback only after done and audio drain', async () => {
  const h = createHarness();
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle(); h.resolveCamera(); await voice;
  const ws = FakeWebSocket.instances[0];
  ws.emit({type: 'session.created', session: {output_audio_samplerate: 24000}});
  ws.emit({type: 'response.created', response: {id: 'r1', turn_id: 't1', unsolicited: false}});
  ws.emit({type: 'response.audio.delta', response_id: 'r1', delta: 'AQAAAA=='});
  ws.emit({type: 'response.done', response: {id: 'r1', status: 'completed'}});
  assert.deepEqual(ws.sent.filter(message => message.type === 'playback.update'), [
    {type: 'playback.update', response_id: 'r1', playing: true},
  ]);
  FakeAudioContext.instances[0].sources.at(-1).onended();
  assert.deepEqual(ws.sent.filter(message => message.type === 'playback.update'), [
    {type: 'playback.update', response_id: 'r1', playing: true},
    {type: 'playback.update', response_id: 'r1', playing: false},
  ]);
});

test('typed text and images use the active realtime session and interrupt local playback', async () => {
  const h = createHarness();
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle(); h.resolveCamera(); await voice;
  const ws = FakeWebSocket.instances[0];
  ws.emit({type: 'session.created', session: {output_audio_samplerate: 24000}});
  ws.emit({type: 'response.created', response: {id: 'old', turn_id: 'old-turn', unsolicited: false}});
  ws.emit({type: 'response.audio.delta', response_id: 'old', delta: 'AQAAAA=='});
  h.document.getElementById('chat-input').value = 'What changed?';
  h.sandbox.__spa.setPendingImage({url: 'data:image/jpeg;base64,anBlZw==', width: 800, height: 450, name: 'scene.jpg'});
  await h.sandbox.__spa.sendChat();

  assert.equal(FakeAudioContext.instances[0].sources.at(-1).stopped, true);
  assert.ok(ws.sent.some(message => message.type === 'playback.update' && message.response_id === 'old' && message.playing === false));
  const item = ws.sent.find(message => message.type === 'conversation.item.create' && message.item.role === 'user');
  assert.deepEqual(item.item.content, [
    {type: 'input_text', text: 'What changed?'},
    {type: 'input_image', image_url: 'data:image/jpeg;base64,anBlZw=='},
  ]);
  assert.equal(ws.sent.at(-1).type, 'response.create');
  assert.equal(h.fetchCalls.some(([url]) => url === '/api/chat'), false);
});

test('late text, tool, truncation, and done events cannot take over a newer response', async () => {
  const h = createHarness();
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle(); h.resolveCamera(); await voice;
  const ws = FakeWebSocket.instances[0];
  ws.emit({type: 'session.created', session: {output_audio_samplerate: 24000}});
  ws.emit({type: 'response.created', response: {id: 'old', turn_id: 'old-turn', unsolicited: false}});
  ws.emit({type: 'response.created', response: {id: 'new', turn_id: 'new-turn', unsolicited: false}});
  ws.emit({type: 'response.output_text.delta', response_id: 'new', delta: 'current'});
  ws.emit({type: 'response.output_text.delta', response_id: 'old', delta: ' ghost'});
  ws.emit({type: 'response.function_call_arguments.done', response_id: 'old', call_id: 'call-old', name: 'camera', arguments: '{}'});
  ws.emit({type: 'conversation.item.truncated', item_id: 'old'});
  ws.emit({type: 'response.done', response: {id: 'old', status: 'cancelled'}});

  assert.equal(h.sandbox.__spa.getRt().replyLine, 'current');
  assert.equal(h.sandbox.__spa.getRt().pendingCall, null);
  assert.equal(h.sandbox.__spa.getRt().presence.accepts('new'), true);
});

test('speech interruption retires the response and clears its pending camera call before a replacement exists', async () => {
  const h = createHarness();
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle(); h.resolveCamera(); await voice;
  const ws = FakeWebSocket.instances[0];
  ws.emit({type: 'session.created', session: {output_audio_samplerate: 24000}});
  ws.emit({type: 'response.created', response: {id: 'old', turn_id: 'old-turn', unsolicited: false}});
  ws.emit({type: 'response.function_call_arguments.done', response_id: 'old', call_id: 'old-call', name: 'camera', arguments: '{}'});
  ws.emit({type: 'input_audio_buffer.speech_started'});
  const sentAtInterruption = ws.sent.length;
  ws.emit({type: 'response.output_text.delta', response_id: 'old', delta: 'ghost'});
  ws.emit({type: 'response.audio.delta', response_id: 'old', delta: 'AQAAAA=='});
  ws.emit({type: 'response.done', response: {id: 'old', status: 'completed'}});

  assert.equal(h.sandbox.__spa.getRt().presence.accepts('old'), false);
  assert.equal(h.sandbox.__spa.getRt().pendingCall, null);
  assert.equal(h.sandbox.__spa.getRt().replyLine, '');
  assert.equal(ws.sent.length, sentAtInterruption);
});

test('stale voice startup cannot release or leak into a replacement startup', async () => {
  const h = createHarness({perceptionEnabled: false});
  const stale = h.sandbox.__spa.startVoiceMode();
  await h.settle();
  const staleSocket = FakeWebSocket.instances[0];
  const staleMic = h.micTracks[0];
  h.sandbox.__spa.stopVoiceMode();

  const replacement = h.sandbox.__spa.startVoiceMode();
  await h.settle();
  const replacementSocket = FakeWebSocket.instances[1];
  const replacementMic = h.micTracks[1];
  h.resolveCamera();
  await Promise.all([stale, replacement]);

  assert.equal(staleSocket.readyState, 3);
  assert.equal(staleMic.readyState, 'ended');
  assert.equal(replacementSocket.readyState, 1);
  assert.equal(replacementMic.readyState, 'live');
  assert.equal(h.cameraTrack.readyState, 'live');
  assert.equal(h.sandbox.__spa.getRt().ws, replacementSocket);
});

test('camera permission failure preserves a negotiated voice-only session', async () => {
  const h = createHarness();
  const voice = h.sandbox.__spa.startVoiceMode();
  await h.settle();
  h.cameraPending.reject(new Error('denied'));
  await voice;
  const ws = FakeWebSocket.instances[0];
  ws.emit({type: 'session.created', session: {output_audio_samplerate: 24000}});
  const update = ws.sent.find(message => message.type === 'session.update');

  assert.equal(h.sandbox.__spa.getRt().video, null);
  assert.equal(h.sandbox.__spa.getRt().micStream.getAudioTracks()[0], h.micTracks[0]);
  assert.equal(update.session.visual_context, false);
  assert.deepEqual(update.session.tools, []);
});
