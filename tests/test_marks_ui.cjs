// Run with: node --test tests/test_marks_ui.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function app() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', files: [], disabled: false, textContent: '', innerHTML: '', style: {},
      classList: {add() {}, remove() {}, toggle() {}},
      querySelector: () => element(id + ':child'), appendChild() {},
    });
    return elements.get(id);
  }
  let now = 1000000;
  const calls = [];
  const context = vm.createContext({
    document: {getElementById: element, querySelector: () => ({value: 'upload'}), querySelectorAll: () => [],
               createElement: () => element('new-row')},
    Date: {now: () => now}, setTimeout: () => 1, clearTimeout() {},
    setInterval: () => 1, clearInterval() {}, FormData, Headers,
    fetch: async (url) => {calls.push(url); throw new Error('Unexpected request');},
  });
  // App initialization is followed by category helpers; remove just the two
  // startup calls while keeping all helper declarations in the test context.
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8')
    .replace(/^init\(\);[ \t]*\r?$/m, '')
    .replace(/^initAuth\(\);[ \t]*\r?$/m, '');
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  const file = new File(['%PDF-test'], 'test.pdf', {type: 'application/pdf'});
  element('qpFile').files = [file];
  element('akFile').files = [file];
  element('studentClass').value = 'Class 11';
  element('subject').value = 'Physics';
  element('totalMarks').value = '3';
  context.file = file;
  run('state.googleToken = "test"; state.sheets = [file];');
  return {run, element, calls, context, advance: ms => {now += ms;}};
}

const rows = [{qid: 'Q1', max: 1}, {qid: 'Q2', max: 2}];
function adopt(ui, extra) {
  const result = {items: rows, total: 3, effective_total: 3, ...extra};
  ui.run(`adoptMarksResponse(${JSON.stringify(result)}); refreshChecklist();`);
}

test('a rate limit pauses actions then allows verification without authorizing grading', async () => {
  const ui = app();
  adopt(ui, {method: 'ai_verifier_busy', verification_detail: 'Claude is temporarily busy',
             failure_code: 'verifier_rate_limited', retry_after_seconds: 60});
  assert.equal(ui.element('evaluate').disabled, true);
  assert.match(ui.element('checklist').innerHTML, /Claude is temporarily busy/);
  await ui.run('evaluate()');
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.element('detectMarks').disabled, true);
  ui.advance(61000);
  ui.run('refreshChecklist()');
  assert.equal(ui.element('detectMarks').disabled, false);
  assert.equal(ui.element('detectMarks').textContent, 'Retry marks verification');
  assert.equal(ui.element('evaluate').disabled, false);
  assert.equal(ui.element('evaluate').textContent, 'Verify marks & evaluate');
  assert.equal(ui.run('marksAreVerified()'), false);
});

test('live proof enables grading and stale proof offers automatic verification', () => {
  const ui = app();
  adopt(ui, {method: 'ai_verified', marks_attestation: 'proof', marks_attestation_expires_at: 1100});
  assert.equal(ui.element('evaluate').disabled, false);
  ui.advance(101000);
  ui.run('refreshChecklist()');
  assert.equal(ui.element('evaluate').disabled, false);
  assert.equal(ui.element('evaluate').textContent, 'Verify marks & evaluate');
  assert.equal(ui.run('marksAreVerified()'), false);
  assert.equal(ui.run('state.marksAttestation'), '');
});

test('a proof without expiry remains verified after more than 24 hours', () => {
  const ui = app();
  adopt(ui, {method: 'ai_verified', marks_attestation: 'continuous-proof', marks_attestation_expires_at: null});
  assert.equal(ui.run('marksAreVerified()'), true);
  ui.advance(25 * 60 * 60 * 1000);
  ui.run('refreshChecklist()');
  assert.equal(ui.run('marksAreVerified()'), true);
  assert.equal(ui.run('state.marksAttestation'), 'continuous-proof');
  assert.equal(ui.element('evaluate').disabled, false);
  assert.equal(ui.calls.length, 0);
  // Removing the time limit must not make changed marks authoritative.
  ui.element('totalMarks').value = '4';
  assert.equal(ui.run('marksAreVerified()'), false);
});

test('deterministic marks remain free but wrong totals and unresolved choices block', () => {
  const ui = app();
  adopt(ui, {method: 'regex'});
  assert.equal(ui.element('evaluate').disabled, false);
  ui.element('totalMarks').value = '4';
  ui.run('refreshChecklist()');
  assert.equal(ui.run('marksAreVerified()'), false);
  ui.element('totalMarks').value = '3';
  adopt(ui, {method: 'regex', choice_detected: true});
  assert.equal(ui.run('marksAreVerified()'), false);
  adopt(ui, {method: 'ai_verified', marks_attestation: 'proof', marks_attestation_expires_at: 1100,
             items: [...rows, {qid: 'Q3', max: 2}], choice_detected: true,
             choice_verified: true, choice_groups: [{}], effective_total: 3});
  assert.equal(ui.element('evaluate').disabled, false);
});

test('verification is single flight and recovery unlocks Evaluate', async () => {
  const ui = app();
  let release;
  ui.context.fetch = async url => {
    ui.calls.push(url);
    return new Promise(resolve => {release = resolve;});
  };
  const first = ui.run('detectMarks()');
  await ui.run('detectMarks()');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.element('evaluate').disabled, true);
  release({ok: true, json: async () => ({items: rows, total: 3, method: 'ai_verified',
    effective_total: 3, marks_attestation: 'proof', marks_attestation_expires_at: 1100})});
  await first;
  assert.equal(ui.element('evaluate').disabled, false);
});

test('nested CA choices display effective 100 with 118 offered marks after verification', () => {
  const ui = app();
  ui.element('totalMarks').value = '100';
  const items = [{qid: 'Q1', max: 44}, ...[2, 3, 4, 5].map(i => ({qid: `Q${i}`, max: 14})),
                 {qid: 'Q6.a', max: 5}, {qid: 'Q6.b', max: 5},
                 {qid: 'Q6.c1', max: 4}, {qid: 'Q6.c2', max: 4}];
  adopt(ui, {items, method: 'ai_verified', marks_attestation: 'nested-proof',
             marks_attestation_expires_at: 1100, choice_detected: true,
             choice_verified: true, choice_groups: [{}, {}], effective_total: 100});
  ui.run('updateLockedTotal()');
  assert.equal(ui.element('evaluate').disabled, false);
  assert.equal(ui.element('lockedTotal').textContent, '100 (verified; all alternatives sum to 118)');
  assert.match(ui.run('marksSummary(9, 118, "ai_verified")'), /2 internal-choice rules/);
  adopt(ui, {items, method: 'ai_mismatch', choice_detected: true,
             verification_detail: 'Extracted 120; printed allocations 118.'});
  assert.equal(ui.run('marksAreVerified()'), false);
  assert.match(ui.element('checklist').innerHTML, /printed allocations 118/);
});

test('a stale server proof response requires fresh verification and is never replayed', async () => {
  const ui = app();
  adopt(ui, {method: 'ai_verified', marks_attestation: 'proof', marks_attestation_expires_at: 1100});
  ui.context.fetch = async url => {
    ui.calls.push(url);
    return {ok: false, status: 400, headers: new Headers(),
            json: async () => ({detail: 'The verified marks proof is missing or expired; detect marks again.'})};
  };
  await ui.run('evaluate()');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.element('evaluate').textContent, 'Verify marks & evaluate');
  assert.equal(ui.run('state.marksAttestation'), '');
  assert.equal(ui.element('detectMarks').textContent, 'Retry marks verification');
});

test('failed CA extraction clears the old table and displays the actual failure', async () => {
  const ui = app();
  ui.element('totalMarks').value = '100';
  adopt(ui, {method: 'ai_mismatch', items: [{qid: 'Q1', max: 120}]});
  ui.context.fetch = async () => ({ok: true, json: async () => ({
    items: [], total: 0, paper_total: 100, method: 'ai_extraction_failed',
    verification_detail: 'Claude stopped before completing the marks reading.',
    failure_code: 'extractor_incomplete_output', marks_attestation: '',
  })});
  await ui.run('detectMarks()');
  assert.equal(ui.run('state.marksItems.length'), 0);
  assert.equal(ui.run('marksAreVerified()'), false);
  assert.equal(ui.element('lockedTotal').textContent, '100 (awaiting complete marks verification)');
  assert.match(ui.element('marksMethod').textContent, /Claude stopped/);
  assert.match(ui.element('checklist').innerHTML, /Claude stopped/);
  assert.equal(ui.element('detectMarks').textContent, 'Retry marks verification');
});

test('one click verifies first and queues only after receiving a valid proof', async () => {
  const ui = app();
  adopt(ui, {method: 'ai_unverified', verification_detail: 'Previous boundary reading failed.'});
  ui.run('pollJob = async () => {};');
  let release;
  ui.context.fetch = async (url, options) => {
    ui.calls.push(url);
    if (url.endsWith('/api/detect-marks')) return new Promise(resolve => {release = resolve;});
    assert.match(url, /\/api\/evaluate$/);
    assert.equal(options.body.get('marks_attestation'), 'fresh-proof');
    assert.equal(options.body.get('total_marks'), '3');
    return {ok: true, json: async () => ({job_id: 'job', download_key: 'key'})};
  };
  const evaluating = ui.run('evaluate()');
  await ui.run('evaluate()');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.element('evaluate').disabled, true);
  release({ok: true, json: async () => ({items: rows, total: 3, effective_total: 3,
    method: 'ai_verified', marks_attestation: 'fresh-proof', marks_attestation_expires_at: 1100})});
  await evaluating;
  assert.equal(ui.calls.length, 2);
  assert.equal(ui.run('state.jobId'), 'job');
});

test('Skills verifies and evaluates with the same hidden-class form and bound proof', async () => {
  const ui = app();
  ui.run('state.categories = [{id: "innovation", level_label: "School of Innovation"}]; pollJob = async () => {};');
  ui.element('paperType').value = 'innovation';
  ui.element('studentClass').value = '';
  ui.element('subject').value = 'Python Fundamentals';
  ui.element('totalMarks').value = '200';
  const items = Array.from({length: 10}, (_, i) => ({qid: `Q${i + 1}`, max: 20}));
  ui.context.fetch = async (url, options) => {
    ui.calls.push(url);
    assert.equal(options.body.get('category'), 'innovation');
    assert.equal(options.body.get('student_class'), '');
    assert.equal(options.body.get('exam_level'), '');
    assert.equal(options.body.get('subject'), 'Python Fundamentals');
    assert.equal(options.body.get('total_marks'), '200');
    if (url.endsWith('/api/detect-marks')) {
      return {ok: true, json: async () => ({items, total: 200, effective_total: 200,
        method: 'ai_verified', marks_attestation: 'skills-proof', marks_attestation_expires_at: 1100})};
    }
    assert.match(url, /\/api\/evaluate$/);
    assert.equal(options.body.get('marks_attestation'), 'skills-proof');
    assert.deepEqual(JSON.parse(options.body.get('marks_items')), items);
    return {ok: true, json: async () => ({job_id: 'skills-job', download_key: 'key'})};
  };
  await ui.run('evaluate()');
  assert.deepEqual(ui.calls, ['/api/detect-marks', '/api/evaluate']);
  assert.equal(ui.run('state.jobId'), 'skills-job');
});

test('one-click verification never submits a failed reading for grading', async () => {
  const ui = app();
  ui.context.fetch = async url => {
    ui.calls.push(url);
    assert.match(url, /\/api\/detect-marks$/);
    return {ok: true, json: async () => ({items: rows, total: 3, method: 'ai_disagreement',
      verification_detail: 'Q2: printed maximum is unresolved.', marks_attestation: ''})};
  };
  await ui.run('evaluate()');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.run('state.jobId'), null);
  assert.match(ui.element('toast').textContent, /Q2/);
  assert.equal(ui.element('evaluate').disabled, false);
});

test('changing the answer key during verification cannot queue an unexpected job', async () => {
  const ui = app();
  let release;
  ui.context.fetch = async url => {
    ui.calls.push(url);
    return new Promise(resolve => {release = resolve;});
  };
  const evaluating = ui.run('evaluate()');
  ui.element('akFile').files = [new File(['changed'], 'different.pdf')];
  release({ok: true, json: async () => ({items: rows, total: 3, effective_total: 3,
    method: 'ai_verified', marks_attestation: 'fresh-proof', marks_attestation_expires_at: 1100})});
  await evaluating;
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.run('state.jobId'), null);
  assert.match(ui.element('toast').textContent, /changed/);
});

test('uncapped results still display actual provider cost without a false ceiling', () => {
  const ui = app();
  const result = {score: 13, max: 13, release: true, questions: [],
    budget: {enforced: false, cap_inr: null, actual_inr: 47.38, rates_found: true, usage_attested: true}};
  const html = ui.run(`detailHtml(${JSON.stringify(result)})`);
  assert.match(html, /₹47\.38/);
  assert.match(html, /No budget cap/);
  assert.doesNotMatch(html, /Infinity|₹15 cap|₹20 cap/);
  result.budget.usage_attested = false;
  const unknown = ui.run(`detailHtml(${JSON.stringify(result)})`);
  assert.match(unknown, /Unavailable/);
  assert.doesNotMatch(unknown, /₹47\.38/);
});
