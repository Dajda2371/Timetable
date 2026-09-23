// Exercise the actual embedded UI script with a small DOM/fetch harness.
// These tests check behavior; they do not simulate browser layout or painting.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '..', 'ui.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].split('// Initial setup')[0];
const response = (status, data) => ({ ok: status >= 200 && status < 300, status, json: async () => data });

function harness(fetch, savedJob) {
    const elements = new Map();
    const storage = new Map(savedJob ? [['timetable_generation_id', savedJob]] : []);
    function element(id) {
        if (!elements.has(id)) elements.set(id, {
            innerHTML: '', textContent: '', value: '', disabled: false, style: {},
            classList: { add() {}, remove() {}, toggle() {} },
            addEventListener() {}, appendChild() {},
        });
        return elements.get(id);
    }
    const context = vm.createContext({
        fetch, console, setTimeout: callback => queueMicrotask(callback),
        document: {
            getElementById: element, createElement: () => element(Symbol()),
            querySelectorAll: () => [element('generateButton')], body: element('body'),
        },
        localStorage: {
            getItem: key => storage.get(key) || null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: key => storage.delete(key),
        },
    });
    vm.runInContext(script, context);
    element('entity-filter-select').value = 'all';
    return { context, element, storage, run: code => vm.runInContext(code, context) };
}

test('generation submits unsaved editor snapshot, polls, and loads success', async () => {
    const calls = [];
    let polls = 0;
    const h = harness(async (url, options) => {
        calls.push({ url, options });
        if (url === '/generation-jobs') return response(202, { job_id: 'job-1' });
        if (url === '/generation-jobs/job-1') {
            return response(200, ++polls === 1 ? { status: 'running', phase: 'finding_feasible', elapsed_seconds: 2 } :
                { status: 'succeeded', outcome: 'feasible', message: 'Saved.', quality_metrics: { class_gaps: 0, subject_repetitions: 0, teacher_gaps: 1 } });
        }
        if (url.startsWith('/timetable')) return response(200, { classes_timetable: {}, teachers_timetable: {} });
        throw new Error(`Unexpected request: ${url}`);
    });
    h.run('config = {unsaved: "current editor"}');
    await h.context.generateAndShowTimetable();
    assert.deepEqual(JSON.parse(calls[0].options.body), { unsaved: 'current editor' });
    assert.equal(calls[0].options.method, 'POST');
    assert.equal(polls, 2);
    assert(calls.some(call => call.url.startsWith('/timetable')));
    assert.match(h.element('status').textContent, /Student gaps: 0/);
    assert.equal(h.element('generateButton').disabled, false);
    assert.equal(h.storage.has('timetable_generation_id'), false);
});

test('validation details are visible and previous timetable is not reloaded', async () => {
    const urls = [];
    const h = harness(async url => {
        urls.push(url);
        return response(422, { detail: { message: 'Invalid configuration.', issues: ['Class A needs too many lessons.'] } });
    });
    await h.context.generateAndShowTimetable();
    assert.match(h.element('status').textContent, /Class A needs too many lessons/);
    assert.deepEqual(urls, ['/generation-jobs']);
    assert.equal(h.element('generateButton').disabled, false);
});

test('a second click cannot submit another active job', async () => {
    let release, submissions = 0;
    const h = harness(async url => {
        if (url === '/generation-jobs') {
            submissions++;
            return new Promise(resolve => { release = () => resolve(response(202, { job_id: 'one' })); });
        }
        return response(200, { status: 'timeout', message: 'No solution found within the budget.' });
    });
    const first = h.context.generateAndShowTimetable();
    assert.equal(h.element('generateButton').disabled, true);
    await h.context.generateAndShowTimetable();
    assert.equal(submissions, 1);
    release();
    await first;
    assert.match(h.element('status').textContent, /No solution found/);
});

test('saved job resumes polling without a new submission and clears on restart', async () => {
    const urls = [];
    const h = harness(async url => {
        urls.push(url);
        return response(404, { detail: 'Job history resets when the server restarts.' });
    }, 'saved');
    await h.context.generateAndShowTimetable();
    assert.deepEqual(urls, ['/generation-jobs/saved']);
    assert.equal(h.storage.size, 0);
    assert.match(h.element('status').textContent, /server restarts/);
});

test('network failure keeps job identity so retry checks the existing job', async () => {
    const h = harness(async () => { throw new Error('Offline'); }, 'saved');
    await h.context.generateAndShowTimetable();
    assert.equal(h.storage.get('timetable_generation_id'), 'saved');
    assert.match(h.element('status').textContent, /may still be running/);
});

test('viewer uses generated configuration and renders per-class lunch', () => {
    const h = harness(async () => { throw new Error('Unexpected request'); });
    h.run(`
        config = {schedule_config: {WrongDay: {max_periods: 1}}};
        timetableData = {
            classes_timetable: {A: {Monday: {'1': {teacher: 'Original teacher', subject: 'Math'}}}},
            teachers_timetable: {},
            metadata: {
                configuration: {classes: [{class_name: 'A', grade: 'Original grade', class_teacher: 'Original teacher'}],
                    schedule_config: {Monday: {max_periods: 2}}},
                class_lunches: {A: {Monday: 2}}
            }
        };
        renderCurrentTimetables();
    `);
    const rendered = h.element('timetables-display-area').innerHTML;
    assert.match(rendered, /Monday/);
    assert.match(rendered, /Original grade/);
    assert.match(rendered, /Lunch/);
    assert.doesNotMatch(rendered, /WrongDay/);
});

test('legacy timetable without metadata remains renderable', () => {
    const h = harness(async () => { throw new Error('Unexpected request'); });
    h.run(`
        config = {schedule_config: {Monday: {max_periods: 2}}};
        timetableData = {classes_timetable: {A: {Monday: {'1': {teacher: 'T', subject: 'Math'}}}}, teachers_timetable: {}};
        renderCurrentTimetables();
    `);
    assert.match(h.element('timetables-display-area').innerHTML, /Math/);
});

test('editor keeps period values numeric and does not truncate invalid hours', () => {
    const h = harness(async () => { throw new Error('Unexpected request'); });
    h.run(`
        config = {schedule_config: {Monday: {}}, time_grant: {'1': {Math: 0}}};
        updateSchedule('Monday', 'max_periods', '6');
        updateTimeGrant('1', 'Math', '1.5');
    `);
    assert.equal(h.run('config.schedule_config.Monday.max_periods'), 6);
    assert.equal(h.run('config.time_grant["1"].Math'), 1.5);
});
