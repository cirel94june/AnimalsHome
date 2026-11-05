"""11-03：淘宝联盟官方接口（服务器上搜商品），替换原作者写死本机路径、服务器上用不了的工具。"""

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import taobao_shopping
import taobao_union
from taobao_shopping import TaobaoStore

LEGACY_ITEM = {"item_id": "aBc12-xyz9", "title": "保温杯 316 不锈钢", "pict_url": "//img.alicdn.com/i1/x.jpg",
               "zk_final_price": "59.9", "shop_title": "某某旗舰店", "url": "//s.click.taobao.com/abc"}
UPGRADE_ITEM = {"item_id": "Zz9-88kk", "item_basic_info": {"title": "懒人投影支架", "pict_url": "https://img.alicdn.com/a.jpg",
                                                         "shop_title": "家居小铺"},
                "price_promotion_info": {"final_promotion_price": "39", "zk_final_price": "45"},
                "publish_info": {"click_url": "https://s.click.taobao.com/t?e=1"}}


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


def fake_client(answers, calls):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            calls.append(dict(data))
            return FakeResponse(answers.pop(0))
    return Client


class UnionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.secret_path = Path(self.tmp.name) / "taobao_union_secret.json"
        self.patch = patch.object(taobao_union, "SECRET_PATH", self.secret_path)
        self.patch.start()
        taobao_union.save_secret("s3cretValue123")

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_signature_follows_taobao_md5_rule(self):
        params = {"method": "m", "app_key": "123", "v": "2.0"}
        expected = hashlib.md5(b"S" + b"app_key123methodmv2.0" + b"S").hexdigest().upper()
        self.assertEqual(expected, taobao_union.sign(params, "S"))

    def test_both_response_shapes_become_safe_products(self):
        a = taobao_union.normalize(LEGACY_ITEM)
        self.assertEqual(("aBc12-xyz9", "https://s.click.taobao.com/abc", "https://img.alicdn.com/i1/x.jpg", "59.9"),
                         (a["item_id"], a["url"], a["image"], a["price"]))
        b = taobao_union.normalize(UPGRADE_ITEM)
        self.assertEqual(("懒人投影支架", "39", "家居小铺"), (b["title"], b["price"], b["shop"]))
        # 外站链接、图片不是阿里的、没标题的都不要
        self.assertIsNone(taobao_union.normalize({**LEGACY_ITEM, "url": "https://evil.example/x"}))
        self.assertEqual("", taobao_union.normalize({**LEGACY_ITEM, "pict_url": "https://evil.example/a.jpg"})["image"])
        self.assertIsNone(taobao_union.normalize({**LEGACY_ITEM, "title": ""}))

    def test_tricky_links_are_rejected_without_breaking_the_search(self):
        """Codex §139 L1：反斜杠、坏端口、解析不了的链接，服务器要拒，而且只跳过那一件。"""
        for bad in ("https://evil.example\\x.taobao.com/", "https://s.click.taobao.com:bad/x", "https://[bad",
                    "https://s.click.taobao.com:8080/x", "https://s.click.taobao.com/a b"):
            self.assertIsNone(taobao_union.normalize({**LEGACY_ITEM, "url": bad}), bad)
        self.assertIsNotNone(taobao_union.normalize({**LEGACY_ITEM, "url": "https://s.click.taobao.com:443/x"}))
        calls = []
        answers = [{"tbk_dg_material_optional_upgrade_response": {"result_list": {"map_data": [
            {**LEGACY_ITEM, "url": "https://[bad"}, UPGRADE_ITEM]}}}]
        with patch.object(taobao_union, "_client", fake_client(answers, calls)):
            result = asyncio.run(taobao_union.search("x", app_key="12345678", adzone_id="1111111"))
        self.assertEqual((1, 1), (len(result["products"]), result["skipped"]))

    def test_falls_back_to_the_approved_legacy_search_when_upgrade_is_not_granted(self):
        calls = []
        answers = [{"error_response": {"code": 11, "msg": "Insufficient isv permissions"}},
                   {"tbk_dg_material_optional_response": {"result_list": {"map_data": [LEGACY_ITEM, {"title": "坏的"}]}}}]
        with patch.object(taobao_union, "_client", fake_client(answers, calls)):
            result = asyncio.run(taobao_union.search("保温杯", app_key="12345678", adzone_id="1111111"))
        self.assertEqual([taobao_union.METHOD_UPGRADE, taobao_union.METHOD_LEGACY], [c["method"] for c in calls])
        self.assertEqual(taobao_union.METHOD_LEGACY, result["method"])
        self.assertEqual(1, len(result["products"]))
        self.assertEqual(1, result["skipped"])
        self.assertTrue(all("s3cretValue123" not in json.dumps(c) for c in calls))  # 钥匙不出现在请求里，只有签名

    def test_other_errors_are_not_silently_retried(self):
        calls = []
        answers = [{"error_response": {"code": 7, "msg": "App Call Limited"}}]
        with patch.object(taobao_union, "_client", fake_client(answers, calls)):
            with self.assertRaises(taobao_union.UnionError):
                asyncio.run(taobao_union.search("x", app_key="12345678", adzone_id="1111111"))
        self.assertEqual(1, len(calls))

    def test_no_result_error_is_an_empty_list_not_a_failure(self):
        """11-05：Lucien 逛「仓鼠亚克力观景台 磁吸 可拆洗」，接口回 code 15「无结果」，原来整次逛街算失败。"""
        calls = []
        answers = [{"error_response": {"code": 15, "msg": "Remote service error", "sub_msg": "无结果"}}]
        with patch.object(taobao_union, "_client", fake_client(answers, calls)):
            result = asyncio.run(taobao_union.search("仓鼠", app_key="12345678", adzone_id="1111111"))
        self.assertEqual([], result["products"])
        self.assertEqual(0, result["skipped"])

    def test_other_code_15_errors_still_fail(self):
        answers = [{"error_response": {"code": 15, "msg": "Remote service error", "sub_msg": "服务繁忙"}}]
        with patch.object(taobao_union, "_client", fake_client(answers, [])):
            with self.assertRaises(taobao_union.UnionError):
                asyncio.run(taobao_union.search("仓鼠", app_key="12345678", adzone_id="1111111"))

    def test_broadening_drops_last_words_until_something_is_found(self):
        calls = []
        no = {"error_response": {"code": 15, "msg": "Remote service error", "sub_msg": "无结果"}}
        answers = [no, no, {"tbk_dg_material_optional_upgrade_response": {"result_list": {"map_data": [UPGRADE_ITEM]}}}]
        with patch.object(taobao_union, "_client", fake_client(answers, calls)):
            result = asyncio.run(taobao_union.search_broadening("仓鼠亚克力观景台 磁吸 可拆洗",
                                                                app_key="12345678", adzone_id="1111111"))
        self.assertEqual(["仓鼠亚克力观景台 磁吸 可拆洗", "仓鼠亚克力观景台 磁吸", "仓鼠亚克力观景台"], [c["q"] for c in calls])
        self.assertEqual("仓鼠亚克力观景台", result["keyword_used"])
        self.assertEqual(1, len(result["products"]))

    def test_broadening_stops_at_one_word_and_at_most_two_drops(self):
        calls = []
        no = {"error_response": {"code": 15, "sub_msg": "无结果"}}
        with patch.object(taobao_union, "_client", fake_client([no], calls)):
            result = asyncio.run(taobao_union.search_broadening("仓鼠", app_key="12345678", adzone_id="1111111"))
        self.assertEqual(1, len(calls))
        self.assertEqual("仓鼠", result["keyword_used"])
        calls = []
        with patch.object(taobao_union, "_client", fake_client([no, no, no], calls)):
            result = asyncio.run(taobao_union.search_broadening("a b c d e", app_key="12345678", adzone_id="1111111"))
        self.assertEqual(["a b c d e", "a b c d", "a b c"], [c["q"] for c in calls])
        self.assertEqual([], result["products"])


class UnionSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(taobao_union, "SECRET_PATH", Path(self.tmp.name) / "secret.json")
        self.patch.start()
        self.store = TaobaoStore(Path(self.tmp.name) / "taobao.sqlite3")
        await self.store.init()

    async def asyncTearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    async def test_secret_is_kept_aside_and_never_returned(self):
        with self.assertRaises(ValueError):  # 用联盟就要填齐
            await self.store.set_settings({"transport": "union", "union_app_key": "12345678", "union_adzone_id": "1111"})
        saved = await self.store.set_settings({"transport": "union", "union_app_key": "12345678",
                                               "union_adzone_id": "1111111", "union_app_secret": "abcdEFGH1234",
                                               "autonomy_enabled": True})
        self.assertTrue(saved["union_secret_set"])
        settings = await self.store.settings()
        self.assertNotIn("abcdEFGH1234", json.dumps(settings))
        self.assertNotIn("union_app_secret", settings)
        # 再保存时留空 = 沿用已存的
        again = await self.store.set_settings({"transport": "union", "union_app_key": "12345678",
                                               "union_adzone_id": "1111111", "autonomy_enabled": True})
        self.assertTrue(again["union_secret_set"])
        with self.assertRaises(ValueError):
            await self.store.set_settings({"transport": "union", "union_app_key": "bad key!",
                                           "union_adzone_id": "1111111"})

    async def test_union_search_records_candidates(self):
        await self.store.set_settings({"transport": "union", "union_app_key": "12345678", "union_adzone_id": "1111111",
                                       "union_app_secret": "abcdEFGH1234", "autonomy_enabled": True})

        async def fake_search(keyword, **kwargs):
            return {"products": [taobao_union.normalize(UPGRADE_ITEM)], "skipped": 2, "method": taobao_union.METHOD_LEGACY}

        with patch.object(taobao_union, "search", fake_search):
            result = await taobao_shopping.search_and_record(self.store, "投影支架")
        self.assertEqual(1, len(result["products"]))
        self.assertEqual(2, result["skipped"])
        self.assertEqual("union", result["transport"])
        saved = await self.store.save_item("aion", result["products"][0]["id"], reflection="好用")
        self.assertEqual("https://s.click.taobao.com/t?e=1", saved["url"])


if __name__ == "__main__":
    unittest.main()
