import assert from 'node:assert/strict';
import test from 'node:test';


test('createOrder forwards host-supplied user identity to the standalone API', async () => {
  globalThis.__ULTIMAPAY_API__ = 'http://127.0.0.1:8061';
  let request;
  globalThis.fetch = async (url, init) => {
    request = { url, init };
    return {
      json: async () => ({
        code: 0,
        data: { orderNo: 'ORDER-1', status: 'pending' },
      }),
    };
  };

  const { createOrder } = await import('../src/api.ts');
  await createOrder('alice', 'signed-by-host', 'SP-01', 'TRON', 'browser-key');

  assert.equal(request.url, 'http://127.0.0.1:8061/api/pay/order');
  assert.equal(request.init.headers['X-Pay-User'], 'alice');
  assert.equal(request.init.headers['X-Pay-Signature'], 'signed-by-host');
  assert.equal(request.init.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(request.init.body), {
    user: 'alice',
    planId: 'SP-01',
    channel: 'TRON',
    idempotencyKey: 'browser-key',
  });
});
