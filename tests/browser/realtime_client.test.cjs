const test = require('node:test');
const assert = require('node:assert/strict');

const {
  RealtimePresenceController,
  SharedCamera,
  FrameUploader,
  createBrowserSourceId,
} = require('../../src/richard/web/realtime_client.js');

class FakeClock {
  constructor() { this.nowMs = 0; this.nextId = 1; this.jobs = new Map(); }
  now = () => this.nowMs;
  setTimeout = (fn, delay) => {
    const id = this.nextId++;
    this.jobs.set(id, {at: this.nowMs + delay, fn});
    return id;
  };
  clearTimeout = id => this.jobs.delete(id);
  advance(ms) {
    const end = this.nowMs + ms;
    for (;;) {
      const due = [...this.jobs.entries()]
        .filter(([, job]) => job.at <= end)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) break;
      this.nowMs = due[1].at;
      this.jobs.delete(due[0]);
      due[1].fn();
    }
    this.nowMs = end;
  }
}

function bank(fingerprint = 'voice-a') {
  return {
    fingerprint,
    clips: [
      {id: 'think-1', phase: 'thinking', decoded: {duration: 0.4}},
      {id: 'think-2', phase: 'thinking', decoded: {duration: 0.5}},
      {id: 'look-1', phase: 'visual', decoded: {duration: 0.6}},
      {id: 'look-2', phase: 'visual', decoded: {duration: 0.7}},
    ],
  };
}

function harness(options = {}) {
  const clock = new FakeClock();
  const played = [];
  const stopped = [];
  const sent = [];
  const controller = new RealtimePresenceController({
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    random: options.random || (() => 0),
    send: message => sent.push(message),
    playClip: (clip, onEnded) => {
      const handle = {clip, onEnded};
      played.push(handle);
      return handle;
    },
    stopClip: handle => stopped.push(handle.clip.id),
  });
  controller.setBank(bank());
  return {clock, controller, played, stopped, sent};
}

function begin(h, responseId = 'r1', turnId = 't1', phase = 'thinking') {
  h.controller.responseCreated({id: responseId, turn_id: turnId, unsolicited: false});
  h.controller.activity({response_id: responseId, turn_id: turnId, phase, unsolicited: false});
}

test('fast answer keeps the prepared bank silent', () => {
  const h = harness();
  begin(h);
  h.clock.advance(1199);
  h.controller.realAudioScheduled('r1');
  h.clock.advance(1);
  assert.equal(h.played.length, 0);
  assert.deepEqual(h.sent, [{type: 'playback.update', response_id: 'r1', playing: true}]);
});

test('slow solicited answer plays one thinking cue after 1200 ms', () => {
  const h = harness();
  begin(h);
  h.clock.advance(1199);
  assert.equal(h.played.length, 0);
  h.clock.advance(1);
  assert.equal(h.played.length, 1);
  assert.equal(h.played[0].clip.phase, 'thinking');
});

test('tool and vision continuations share a turn budget and vision uses visual clips', () => {
  const h = harness();
  begin(h, 'r1', 'turn', 'tool');
  h.clock.advance(1200);
  assert.equal(h.played[0].clip.phase, 'thinking');
  h.played[0].onEnded();

  h.controller.responseDone({id: 'r1', status: 'completed'});
  h.controller.responseCreated({id: 'r2', turn_id: 'turn', unsolicited: false});
  h.controller.activity({response_id: 'r2', turn_id: 'turn', phase: 'vision', unsolicited: false});
  h.clock.advance(7999);
  assert.equal(h.played.length, 1);
  h.clock.advance(1);
  assert.equal(h.played.length, 2);
  assert.equal(h.played[1].clip.phase, 'visual');
  h.played[1].onEnded();

  h.controller.responseCreated({id: 'r3', turn_id: 'turn', unsolicited: false});
  h.controller.activity({response_id: 'r3', turn_id: 'turn', phase: 'thinking', unsolicited: false});
  h.clock.advance(20_000);
  assert.equal(h.played.length, 2);
});

test('one cue plays per audible gap, session cooldown is eight seconds, and variants do not repeat', () => {
  const h = harness();
  begin(h);
  h.clock.advance(1200);
  assert.equal(h.played[0].clip.id, 'think-1');
  h.clock.advance(6000);
  assert.equal(h.played.length, 1);

  h.played[0].onEnded();
  h.controller.activity({response_id: 'r1', turn_id: 't1', phase: 'thinking', unsolicited: false});
  h.clock.advance(1999);
  assert.equal(h.played.length, 1);
  h.clock.advance(1);
  assert.equal(h.played[1].clip.id, 'think-2');
});

test('speech, real audio, terminal failure, and disconnect stop or cancel cues', () => {
  for (const action of ['speech', 'audio', 'failed', 'disconnect']) {
    const h = harness();
    begin(h);
    h.clock.advance(1200);
    assert.equal(h.played.length, 1);
    if (action === 'speech') h.controller.speechStarted();
    if (action === 'audio') h.controller.realAudioScheduled('r1');
    if (action === 'failed') h.controller.responseDone({id: 'r1', status: 'failed'});
    if (action === 'disconnect') h.controller.disconnect();
    assert.deepEqual(h.stopped, ['think-1'], action);
  }
});

test('late activity, text, audio, and terminal events from an old response are rejected', () => {
  const h = harness();
  begin(h, 'old', 'turn-old');
  begin(h, 'new', 'turn-new');
  h.controller.activity({response_id: 'old', turn_id: 'turn-old', phase: 'vision', unsolicited: false});
  assert.equal(h.controller.accepts('old'), false);
  assert.equal(h.controller.accepts('new'), true);
  assert.equal(h.controller.realAudioScheduled('old'), false);
  h.controller.responseDone({id: 'old', status: 'cancelled'});
  h.clock.advance(1200);
  assert.equal(h.played[0].clip.phase, 'thinking');
});

test('error activity cancels waiting speech but later answer PCM still owns playback', () => {
  const h = harness();
  begin(h);
  h.clock.advance(600);
  h.controller.activity({response_id: 'r1', turn_id: 't1', phase: 'error', unsolicited: false});
  h.clock.advance(5000);
  assert.equal(h.played.length, 0);
  assert.equal(h.controller.realAudioScheduled('r1'), true);
  h.controller.responseDone({id: 'r1', status: 'completed'});
  assert.equal(h.sent.length, 1);
  h.controller.realAudioEnded('r1');
  assert.deepEqual(h.sent, [
    {type: 'playback.update', response_id: 'r1', playing: true},
    {type: 'playback.update', response_id: 'r1', playing: false},
  ]);
});

test('playback completion waits for response.done and every scheduled source', () => {
  const h = harness();
  begin(h);
  h.controller.realAudioScheduled('r1');
  h.controller.realAudioScheduled('r1');
  h.controller.realAudioEnded('r1');
  h.controller.responseDone({id: 'r1', status: 'completed'});
  assert.deepEqual(h.sent, [{type: 'playback.update', response_id: 'r1', playing: true}]);
  h.controller.realAudioEnded('r1');
  assert.deepEqual(h.sent.at(-1), {type: 'playback.update', response_id: 'r1', playing: false});
});

test('speech releases acknowledged playback even when its last source already ended', () => {
  const h = harness();
  begin(h);
  h.controller.realAudioScheduled('r1');
  h.controller.realAudioEnded('r1');
  h.controller.speechStarted();
  assert.deepEqual(h.sent.at(-1), {type: 'playback.update', response_id: 'r1', playing: false});
});

test('changing bank identity stops an old-voice cue and missing bank stays silent', () => {
  const h = harness();
  begin(h);
  h.clock.advance(1200);
  h.controller.setBank({fingerprint: 'voice-b', clips: []});
  assert.deepEqual(h.stopped, ['think-1']);
  h.played[0].onEnded();
  h.controller.responseCreated({id: 'r2', turn_id: 't2', unsolicited: false});
  h.controller.activity({response_id: 'r2', turn_id: 't2', phase: 'thinking', unsolicited: false});
  h.clock.advance(20_000);
  assert.equal(h.played.length, 1);
});

test('rechecking the same fingerprint with a now-missing bank stops its cue', () => {
  const h = harness();
  begin(h);
  h.clock.advance(1200);
  h.controller.setBank({fingerprint: 'voice-a', clips: []});
  assert.deepEqual(h.stopped, ['think-1']);
});

test('unsolicited responses never play waiting cues', () => {
  const h = harness();
  h.controller.responseCreated({id: 'r1', turn_id: 't1', unsolicited: true});
  h.controller.activity({response_id: 'r1', turn_id: 't1', phase: 'thinking', unsolicited: true});
  h.clock.advance(20_000);
  assert.equal(h.played.length, 0);
});

test('an empty protocol response without turn metadata still reaches its terminal boundary', () => {
  const h = harness();
  assert.equal(h.controller.responseCreated({id: 'empty'}), true);
  assert.equal(h.controller.responseDone({id: 'empty', status: 'completed'}), true);
  assert.equal(h.played.length, 0);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function fakeVideoTrack(name = 'camera') {
  return {kind: 'video', name, stopped: 0, readyState: 'live', stop() { this.stopped++; this.readyState = 'ended'; }};
}

test('shared camera coalesces concurrent owners and stops only after both release', async () => {
  const track = fakeVideoTrack();
  let requests = 0;
  const camera = new SharedCamera({
    getUserMedia: async () => { requests++; return {getVideoTracks: () => [track], getTracks: () => [track]}; },
    makeVideo: stream => ({stream, videoWidth: 1280, videoHeight: 720, remove() {}, play: async () => {}}),
  });
  const [ambient, voice] = await Promise.all([camera.acquire('ambient'), camera.acquire('voice')]);
  assert.equal(requests, 1);
  assert.equal(ambient.track, track);
  assert.equal(voice.track, track);
  camera.release('voice');
  assert.equal(track.stopped, 0);
  camera.release('ambient');
  assert.equal(track.stopped, 1);
});

test('release during unresolved camera acquisition cannot resurrect that owner', async () => {
  const pending = deferred();
  const track = fakeVideoTrack();
  const camera = new SharedCamera({
    getUserMedia: () => pending.promise,
    makeVideo: stream => ({stream, remove() {}, play: async () => {}}),
  });
  const acquiring = camera.acquire('voice');
  camera.release('voice');
  pending.resolve({getVideoTracks: () => [track], getTracks: () => [track]});
  assert.equal(await acquiring, null);
  assert.equal(track.stopped, 1);
  assert.equal(camera.media(), null);
});

test('frame uploader uses one source, one in-flight request, and forces a fresh post after drain', async () => {
  const first = deferred();
  const uploads = [];
  let captures = 0;
  const uploader = new FrameUploader({
    sourceId: 'browser-page',
    capture: () => `jpeg-${++captures}`,
    upload: (source, image) => { uploads.push([source, image]); return uploads.length === 1 ? first.promise : Promise.resolve(); },
  });
  const initial = uploader.request();
  uploader.request();
  uploader.requestFresh();
  assert.deepEqual(uploads, [['browser-page', 'jpeg-1']]);
  first.resolve();
  await initial;
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(uploads, [
    ['browser-page', 'jpeg-1'],
    ['browser-page', 'jpeg-2'],
  ]);
});

test('frame uploader reports a failed forced follow-up without an unhandled rejection', async () => {
  const first = deferred();
  const errors = [];
  let uploads = 0;
  const uploader = new FrameUploader({
    sourceId: 'browser-page',
    capture: () => 'jpeg',
    upload: () => ++uploads === 1 ? first.promise : Promise.reject(new Error('offline')),
    onError: error => errors.push(error.message),
  });
  const initial = uploader.request();
  uploader.requestFresh();
  first.resolve();
  await initial;
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(errors, ['offline']);
});

test('page source IDs use UUID when available and stay within the server bound', () => {
  const id = createBrowserSourceId({randomUUID: () => '123e4567-e89b-12d3-a456-426614174000'});
  assert.equal(id, 'browser-123e4567-e89b-12d3-a456-426614174000');
  assert.match(id, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/);
  assert.ok(id.length <= 64);
});
