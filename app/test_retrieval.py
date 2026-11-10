# -*- coding: utf-8 -*-
"""用真实知识库验证本地检索和推荐接口，不调用外部模型。"""
import unittest
from unittest.mock import patch

import suggester
from test_support import LinshiShujuTests


class RetrievalTests(LinshiShujuTests):
    modules = (suggester,)

    def test_sales_questions_produce_local_recommendations(self):
        engine = suggester.Tuijianqi()
        cases = [
            ["别家才一千多，你们咋这么贵"],
            ["包过吗？不过的话给退钱吗"],
            ["你们这个授权率到底怎么样啊"],
            ["多久能下证？我着急用"],
            ["要先交钱吗？办下来再付行不行"],
            ["费用太贵了，学生党承担不起"],
            ["你们公司靠不靠谱啊，怕是骗子"],
            ["优审发明7800是吧", "那要是没下来退费吗"],
            ["我是大学老师，想评职称", "评副高需要什么专利"],
            ["这个价格我要一次性付清吗", "能分期吗"],
        ]
        with patch("reranker.requests.post") as post:
            for messages in cases:
                with self.subTest(messages=messages):
                    result = engine.tuijian(messages)
                    self.assertTrue(result["results"])
                    self.assertLessEqual(len(result["results"]), 3)
                    self.assertFalse(result["llm_used"])
                    self.assertIsNone(result["draft"])
        post.assert_not_called()

    def test_empty_context_is_handled_without_network(self):
        with patch("reranker.requests.post") as post:
            result = suggester.Tuijianqi().tuijian(["", "   "])
        self.assertEqual(result["results"], [])
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
