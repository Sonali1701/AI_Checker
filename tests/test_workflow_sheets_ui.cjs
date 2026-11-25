const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function connection() {
  let token = 'app-session', session = 1, oauth, granted = true, now = 1000;
  const calls = [], timers = [], status = {textContent: '', classList: {toggle() {}}};
  const context = vm.createContext({
    window: {WorkflowAuth: {getToken: () => token, getSessionId: () => session}},
    document: {getElementById: () => status},
    Date: {now: () => now},
    setTimeout: callback => {timers.push(callback); return timers.length;}, clearTimeout() {},
    api: async (url, options) => {calls.push({url, options});},
    google: {accounts: {oauth2: {
      hasGrantedAllScopes: (_, scope) => granted && scope === 'https://www.googleapis.com/auth/spreadsheets',
      initTokenClient: options => {oauth = options; return {requestAccessToken() {}};},
    }}},
  });
  context.window.google = context.google;
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/workflow-sheets.js'), 'utf8'), context);
  context.window.WorkflowSheets.configure('test.apps.googleusercontent.com');
  return {
    sheets: context.window.WorkflowSheets, calls, status,
    grant: (seconds = 3600) => oauth.callback({access_token: 'sheet-access', expires_in: seconds}),
    scope: () => oauth.scope, denyScope: () => {granted = false;},
    signOut: () => {token = ''; session += 1; context.window.WorkflowSheets.reset();},
    advance: ms => {now += ms;},
  };
}

test('edit consent registers memory-only access for pending status writes', async () => {
  const ui = connection();
  const pending = ui.sheets.connect();
  assert.equal(ui.scope(), 'https://www.googleapis.com/auth/spreadsheets');
  ui.grant();
  await pending;
  assert.equal(ui.sheets.getToken(), 'sheet-access');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].url, '/api/workflow/sheets/connect');
  assert.equal(ui.calls[0].options.headers['X-Google-Sheets-Token'], 'sheet-access');
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), {expires_in: 3570});
  assert.match(ui.status.textContent, /Copy statuses will be saved/);
  ui.advance(3570001);
  assert.equal(ui.sheets.getToken(), '');
  assert.match(ui.status.textContent, /save pending copy statuses/);
});

test('missing edit scope never registers or exposes access', async () => {
  const ui = connection();
  ui.denyScope();
  const pending = ui.sheets.connect();
  ui.grant();
  await assert.rejects(pending, /edit permission was not granted/);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.sheets.getToken(), '');
});

test('late consent after sign-out cannot register credentials', async () => {
  const ui = connection();
  const pending = ui.sheets.connect();
  ui.signOut();
  ui.grant();
  await assert.rejects(pending, /sign-in changed/);
  assert.equal(ui.calls.length, 0);
  assert.equal(ui.sheets.getToken(), '');
});

test('rerun selection blocks terminal statuses while keeping errors and pending copies', () => {
  const source = fs.readFileSync(path.join(__dirname, '../static/workflow.js'), 'utf8');
  const helper = source.slice(source.indexOf('function completedSheetRow('), source.indexOf('function renderCopies('));
  const context = vm.createContext({});
  vm.runInContext(helper, context);
  for (const status of ['uploaded', ' Rejected ', 'not_downloadable_file', 'already evaluated', 'duplicate', 'superseded']) {
    assert.equal(context.completedSheetRow({workflow_status: status}), true, status);
  }
  for (const status of ['', 'error occurred', 'not login', 'awaiting confirmation', 'faculty review']) {
    assert.equal(context.completedSheetRow({workflow_status: status}), false, status);
  }
  assert.equal(context.completedSheetRow({workflow_status: 'duplicate', skip_processing: true}), true);
});
