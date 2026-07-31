'use strict';

const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const WORKFLOW_PATH = path.join(__dirname, '..', '..', '.github', 'workflows', 'jiffy.yml');

const yaml = fs.readFileSync(WORKFLOW_PATH, 'utf8');

function extractScript(yamlSource) {
  const lines = yamlSource.split('\n');
  const start = lines.findIndex((line) => /^\s*script: \|$/.test(line));
  if (start === -1) {
    throw new Error('Could not find `script: |` block in workflow file');
  }
  const indent = lines[start].match(/^\s*/)[0].length + 2;
  const body = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.trim() === '') {
      body.push('');
      continue;
    }
    if (!line.startsWith(' '.repeat(indent))) break;
    body.push(line.slice(indent));
  }
  return body.join('\n');
}

const SCRIPT = extractScript(yaml);

function makeStubs({ env, actor, existingComments }) {
  const calls = {
    createComment: [],
    reactions: [],
    fetch: [],
    setFailed: [],
    warning: [],
  };

  const github = {
    paginate: async (fn, ...args) => {
      const res = await fn(...args);
      return res.data;
    },
    rest: {
      issues: {
        get: async () => ({
          data: {
            user: { login: 'octocat' },
            body: 'Implement the thing @jiffy',
            created_at: '2026-01-01T00:00:00Z',
          },
        }),
        listComments: async () => ({ data: existingComments }),
        createComment: async (opts) => {
          calls.createComment.push(opts);
          return { data: { id: 1 } };
        },
      },
      reactions: {
        createForIssueComment: async (opts) => {
          calls.reactions.push(opts);
          return { data: {} };
        },
        createForIssue: async (opts) => {
          calls.reactions.push(opts);
          return { data: {} };
        },
      },
    },
  };

  const context = {
    repo: { owner: 'javadib', repo: 'jiffy_gateway' },
    actor,
    eventName: 'issue_comment',
    payload: {
      issue: { number: 42, user: { login: 'octocat' }, body: 'hi @jiffy', created_at: '2026-01-01T00:00:00Z' },
      comment: { id: 7, user: { login: actor } },
    },
  };

  const core = {
    setFailed: (msg) => {
      calls.setFailed.push(msg);
    },
    warning: (msg) => {
      calls.warning.push(msg);
    },
  };

  const fetch = async (url, opts) => {
    calls.fetch.push({ url, opts });
    return { status: 202, text: async () => '{"ok": true}' };
  };

  const sandbox = {
    github,
    context,
    core,
    fetch,
    console,
    process: { env: { ...env } },
    require: (name) => require(name),
  };
  vm.createContext(sandbox);

  const runner = () =>
    vm.runInContext(`(async () => {\n${SCRIPT}\n})()`, sandbox, { timeout: 5000 });

  return { runner, calls };
}

const BASE_ENV = {
  JIFFY_URL: 'https://jiffy.example.com',
  JIFFY_INGEST_TOKEN: 'ingest-secret',
  JIFFY_BOT_LOGIN: 'jiffy-bot',
  REPO_TOKEN: 'repo-pat',
};

async function run() {
  {
    const { runner, calls } = makeStubs({
      env: { ...BASE_ENV, JIFFY_USER_WHITELIST: undefined },
      actor: 'octocat',
      existingComments: [],
    });
    await runner();
    assert.strictEqual(calls.createComment.length, 1, 'one notice comment expected');
    assert.ok(
      calls.createComment[0].body.includes('no `user_whitelist` is configured'),
      'notice must explain that no whitelist is configured'
    );
    assert.ok(
      calls.createComment[0].body.includes('JIFFY_USER_WHITELIST'),
      'notice must include setup instructions'
    );
    assert.ok(
      !calls.createComment[0].body.includes('@jiffy'),
      'notice must not contain the trigger string and re-run the workflow'
    );
    assert.strictEqual(calls.fetch.length, 0, 'must not forward to the gateway');
    assert.strictEqual(calls.reactions.length, 0, 'must not react');
  }

  {
    const { runner, calls } = makeStubs({
      env: { ...BASE_ENV, JIFFY_USER_WHITELIST: '' },
      actor: 'octocat',
      existingComments: [],
    });
    await runner();
    assert.strictEqual(calls.createComment.length, 1, 'empty whitelist behaves like unset');
    assert.strictEqual(calls.fetch.length, 0);
  }

  {
    const { runner, calls } = makeStubs({
      env: { ...BASE_ENV, JIFFY_USER_WHITELIST: 'octocat, jane-doe' },
      actor: 'octocat',
      existingComments: [],
    });
    await runner();
    assert.strictEqual(calls.fetch.length, 1, 'whitelisted user must be forwarded');
    assert.ok(calls.fetch[0].url.endsWith('/api/github/ingestion'));
    assert.strictEqual(calls.createComment.length, 0);
    assert.strictEqual(calls.reactions.length, 1, 'eyes reaction on successful dispatch');
  }

  {
    const { runner, calls } = makeStubs({
      env: { ...BASE_ENV, JIFFY_USER_WHITELIST: 'OCTOCAT' },
      actor: 'octocat',
      existingComments: [],
    });
    await runner();
    assert.strictEqual(calls.fetch.length, 1, 'usernames must be compared case-insensitively');
  }

  {
    const { runner, calls } = makeStubs({
      env: { ...BASE_ENV, JIFFY_USER_WHITELIST: 'jane-doe' },
      actor: 'octocat',
      existingComments: [],
    });
    await runner();
    assert.strictEqual(calls.fetch.length, 0, 'non-whitelisted user must not be forwarded');
    assert.strictEqual(calls.createComment.length, 0, 'non-whitelisted user must get no comment');
    assert.strictEqual(calls.reactions.length, 0, 'non-whitelisted user must get no reaction');
  }

  {
    const existingComments = [
      { body: 'Some unrelated comment' },
      { body: 'no `user_whitelist` is configured' },
    ];
    const { runner, calls } = makeStubs({
      env: { ...BASE_ENV, JIFFY_USER_WHITELIST: '' },
      actor: 'octocat',
      existingComments,
    });
    await runner();
    assert.strictEqual(calls.createComment.length, 0, 'notice must be posted at most once per issue');
    assert.strictEqual(calls.fetch.length, 0);
  }

  {
    const { runner, calls } = makeStubs({
      env: { ...BASE_ENV, JIFFY_USER_WHITELIST: 'alice\n bob ' },
      actor: 'bob',
      existingComments: [],
    });
    await runner();
    assert.strictEqual(calls.fetch.length, 1, 'newline/space separated lists must be accepted');
  }

  console.log('All jiffy workflow user_whitelist tests passed.');
}

run().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
