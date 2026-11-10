import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import requests
import reranker
import storage
import suggester
from test_support import LinshiShujuTests


def response(content, status=200):
    result = requests.Response()
    result.status_code = status
    result.url = "https://provider.example/v1/chat/completions"
    result._content = json.dumps(content).encode("utf-8")
    return result


def completion(content):
    return response({
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


class AuthPipelineTests(LinshiShujuTests):
    modules = (reranker, suggester)

    def setUp(self):
        super().setUp()
        storage.chushihua_yonghu_shuju()
        self.config = {"llm": {"enabled": True, "api_key": "sk-test-secret", "base_url": "https://provider.example/v1"}}
        loader = patch("suggester.jiazai_peizhi", return_value=self.config)
        loader.start()
        self.addCleanup(loader.stop)
        self.engine = suggester.Tuijianqi()
        self.messages = ["费用太贵了，学生党承担不起"]
        hits, _ = self.engine.jiansuoqi.zhineng_sousuo(self.messages)
        self.candidate_id = self.engine.jiansuoqi.huoqu_kuaizi(hits[0][0])["id"]

    def test_successful_rerank_uses_bearer_and_propagates_usage(self):
        with patch("reranker.requests.post", return_value=completion({"picks": [{"id": self.candidate_id, "reason": "匹配"}]})) as post:
            result = self.engine.tuijian(self.messages)
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["results"][0]["id"], self.candidate_id)
        self.assertEqual(result["usage"]["call_tokens"], 15)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer sk-test-secret")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertNotIn("sk-test-secret", json.dumps(result))

    def test_auth_http_and_network_failures_preserve_local_results(self):
        cases = [
            (response({"error": "sk-test-secret"}, 401), "authentication"),
            (response({}, 403), "permission"),
            (response({}, 429), "rate_limit"),
            (response({}, 503), "http_error"),
            (requests.Timeout("sk-test-secret"), "timeout"),
            (requests.ConnectionError("sk-test-secret"), "network"),
        ]
        for value, code in cases:
            with self.subTest(code=code):
                kwargs = {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}
                with patch("reranker.requests.post", **kwargs) as post:
                    result = self.engine.tuijian(self.messages)
                self.assertFalse(result["llm_used"])
                self.assertTrue(result["results"])
                self.assertEqual(result["llm_error"]["code"], code)
                self.assertNotIn("sk-test-secret", json.dumps(result))
                self.assertEqual(post.call_count, 1)

    def test_failed_rerank_does_not_trigger_second_draft_request(self):
        with patch.object(self.engine.jiansuoqi, "zhineng_sousuo", return_value=([(0, 0.1)], [])):
            with patch("reranker.requests.post", return_value=response({}, 401)) as post:
                result = self.engine.tuijian(self.messages)
        self.assertEqual(len(result["results"]), 1)
        self.assertIsNone(result["draft"])
        self.assertEqual(post.call_count, 1)

    def test_disabled_ai_runs_local_retrieval_without_http(self):
        self.engine.chongpaiqi.qiyong = False
        with patch("reranker.requests.post") as post:
            result = self.engine.tuijian(self.messages)
            draft = self.engine.shengcheng_caogao(self.messages)
        post.assert_not_called()
        self.assertTrue(result["results"])
        self.assertFalse(result["llm_used"])
        self.assertEqual(draft["llm_error"]["code"], "disabled")

    def test_insecure_or_ambiguous_endpoints_are_rejected_before_http(self):
        for url in ("http://provider.example/v1", "https://user:pass@provider.example/v1", "https://provider.example/v1?key=secret", "https://provider.example:bad/v1"):
            with self.subTest(url=url), patch("reranker.requests.post") as post:
                self.engine.chongpaiqi.jichu_url = url
                result = self.engine.tuijian(self.messages)
                self.assertEqual(result["llm_error"]["code"], "invalid_url")
                self.assertTrue(result["results"])
                post.assert_not_called()

    def test_redirect_is_not_followed(self):
        with patch("reranker.requests.post", return_value=response({}, 307)) as post:
            result = self.engine.tuijian(self.messages)
        self.assertEqual(result["llm_error"]["code"], "redirect")
        self.assertEqual(post.call_count, 1)

    def test_malformed_model_response_preserves_local_results(self):
        for value in (response({}), completion(["wrong-shape"]), completion({"picks": [None]})):
            with self.subTest(value=value), patch("reranker.requests.post", return_value=value):
                result = self.engine.tuijian(self.messages)
                self.assertTrue(result["results"])
                self.assertEqual(result["llm_error"]["code"], "invalid_response")

    def test_manual_draft_reports_authentication_failure(self):
        with patch("reranker.requests.post", return_value=response({}, 401)):
            result = self.engine.shengcheng_caogao(self.messages)
        self.assertIsNone(result["draft"])
        self.assertEqual(result["llm_error"]["code"], "authentication")

    def test_no_candidates_can_generate_draft_and_report_usage(self):
        with patch.object(self.engine.jiansuoqi, "zhineng_sousuo", return_value=([], [])):
            with patch("reranker.requests.post", return_value=completion({"reply": "您好，请说明具体需求", "used_facts": "公司资料"})):
                result = self.engine.tuijian(self.messages)
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["results"], [])
        self.assertEqual(result["usage"]["call_tokens"], 15)

    def test_rerank_and_draft_usage_are_summed(self):
        with patch("reranker.requests.post", side_effect=[completion({"picks": [], "need_draft": True}), completion({"reply": "请说明需求"})]):
            result = self.engine.tuijian(self.messages)
        self.assertEqual(result["usage"]["call_tokens"], 30)
        self.assertTrue(result["draft"])

    def test_failed_draft_keeps_bm25_candidates(self):
        with patch("reranker.requests.post", side_effect=[completion({"picks": [], "need_draft": True}), response({}, 401)]):
            result = self.engine.tuijian(self.messages)
        self.assertTrue(result["results"])
        self.assertIsNone(result["draft"])
        self.assertEqual(result["llm_error"]["code"], "authentication")

    def test_status_exposes_only_key_presence(self):
        result = self.engine.status()
        self.assertTrue(result["api_key_configured"])
        self.assertNotIn("sk-test-secret", json.dumps(result))

    def test_concurrent_usage_updates_are_not_lost(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: reranker._jilu_yongliang("test", usage), range(16)))
        summary = storage.du_json(reranker.YONGLIANG_LUJING)["summary"]
        self.assertEqual(summary["calls"], 16)
        self.assertEqual(summary["total_tokens"], 240)


if __name__ == "__main__":
    unittest.main()
