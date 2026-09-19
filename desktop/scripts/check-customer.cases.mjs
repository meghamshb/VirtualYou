import test from 'node:test';
import assert from 'node:assert/strict';
import {customerOrigin, preflight} from './customer-preflight.mjs';
test('cannot package blank, private, or credential-bearing service origins', () => {
  for (const url of ['', 'http://service.acme.com', 'https://localhost', 'https://relay.test', 'https://127.0.0.1', 'https://user:secret@service.acme.com', 'https://service.acme.com/path'])
    assert.throws(() => customerOrigin(url));
});
test('release readiness requires a matching complete service response', async () => {
  const status={ready:true, public_origin:'https://service.acme.com', checks:{slack_oauth:true,slack_events:true,model:true}};
  assert.equal(await preflight(status.public_origin, async () => Response.json(status)), status.public_origin);
  await assert.rejects(preflight(status.public_origin, async () => Response.json({...status, ready:false}, {status:503})));
  await assert.rejects(preflight(status.public_origin, async () => Response.json({...status, public_origin:'https://wrong.acme.com'})));
});
