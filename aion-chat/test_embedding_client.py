import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
import memory


class EmbeddingClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.clients = []
        self.config = dict(api_key="first-key", base_url="https://first.test",
                           model="first-model", use_openai=True)
        real_client = httpx.AsyncClient

        async def respond(request):
            self.requests.append(request)
            await asyncio.sleep(0)
            if request.url.host == "generativelanguage.googleapis.com":
                return httpx.Response(200, json={"embedding": {"values": [0.5]}})
            return httpx.Response(200, json={"data": [{"embedding": [0.5]}]})

        def create_client(**kwargs):
            client = real_client(transport=httpx.MockTransport(respond), **kwargs)
            self.clients.append(client)
            return client

        self.factory = patch.object(memory.httpx, "AsyncClient", side_effect=create_client)
        self.cfg = patch.object(memory, "get_embedding_config", side_effect=lambda: self.config)
        self.factory.start()
        self.cfg.start()

    async def asyncTearDown(self):
        close = getattr(memory, "close_embedding_client", None)
        if close:
            await close()
        self.factory.stop()
        self.cfg.stop()

    async def test_concurrent_requests_reuse_client_and_close_releases_it(self):
        results = await asyncio.gather(memory.get_embedding("one"), memory.get_embedding("two"))
        self.assertEqual(results, [[0.5], [0.5]])
        self.assertEqual(len(self.clients), 1)
        self.assertFalse(self.clients[0].is_closed)
        await memory.close_embedding_client()
        self.assertTrue(self.clients[0].is_closed)
        await memory.close_embedding_client()
        self.assertEqual(await memory.get_embedding("again"), [0.5])
        self.assertEqual(len(self.clients), 2)

    async def test_config_changes_apply_without_stale_authorization(self):
        await memory.get_embedding("one")
        self.config.update(base_url="https://second.test", api_key="second-key", model="second-model")
        await memory.get_embedding("two")
        self.assertEqual(str(self.requests[-1].url), "https://second.test/v1/embeddings")
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer second-key")
        self.assertEqual(json.loads(self.requests[-1].content), {"model": "second-model", "input": "two"})
        self.config.update(use_openai=False, api_key="gemini-key", model="gemini-model")
        self.assertEqual(await memory.get_embedding("three"), [0.5])
        self.assertEqual(self.requests[-1].url.params["key"], "gemini-key")
        self.assertEqual(self.requests[-1].url.path, "/v1beta/models/gemini-model:embedContent")
        self.assertNotIn("authorization", self.requests[-1].headers)
        self.assertEqual(len(self.clients), 1)

    async def test_missing_key_does_not_create_client(self):
        self.config["api_key"] = ""
        self.assertIsNone(await memory.get_embedding("one"))
        self.assertEqual(self.clients, [])


if __name__ == "__main__":
    unittest.main()
