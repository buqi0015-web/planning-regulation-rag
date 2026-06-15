from __future__ import annotations

import os
import unittest

os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["LLM_API_KEY"] = ""

from fastapi.testclient import TestClient

from app.api import app
from app.core import ask, ingest_directory, query_wants_figure, query_wants_table, retrieve, split_regulation


class RagCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ingest_directory()

    def test_split_by_article(self) -> None:
        chunks = split_regulation("# 标题\n第一条 内容一。\n第二条 内容二。")
        self.assertEqual([item[0] for item in chunks], ["第一条", "第二条"])

    def test_split_standard_by_clause(self) -> None:
        chunks = split_regulation("前言\n3.0.4 数据选用应符合要求。\n3.0.5 应采用计算机图形文件。")
        self.assertEqual([item[0] for item in chunks], ["前置材料", "3.0.4", "3.0.5"])

    def test_table_intent_detection(self) -> None:
        self.assertTrue(query_wants_table("在建建筑优先选用什么图纸？"))
        self.assertTrue(query_wants_table("公共活动用房的照度值是多少？"))
        self.assertFalse(query_wants_table("日照计算时间间隔是多少？"))

    def test_figure_intent_detection(self) -> None:
        self.assertTrue(query_wants_figure("异型阳台的计算基准面如何确定？"))
        self.assertFalse(query_wants_figure("日照计算时间间隔是多少？"))

    def test_explicit_clause_reference_is_prioritized(self) -> None:
        results = retrieve("《建筑日照计算参数标准》第3.0.5条主要规定了什么？", top_k=3)
        self.assertEqual(results[0]["title"], "建筑日照计算参数标准")
        self.assertEqual(results[0]["section"], "3.0.5")

    def test_retrieve_setback_article(self) -> None:
        results = retrieve("高层建筑沿主干路退让多少米", top_k=3)
        self.assertTrue(any(item["section"] == "第四条" for item in results))

    def test_answer_contains_citations(self) -> None:
        result = ask("申请建设工程规划许可证需要哪些材料？", top_k=3)
        self.assertTrue(result["citations"])
        self.assertIn("[1]", result["answer"])

    def test_demo_page_and_project_status(self) -> None:
        client = TestClient(app)
        page = client.get("/")
        status = client.get("/project-status")
        self.assertEqual(page.status_code, 200)
        self.assertIn("规查", page.text)
        self.assertIn("有什么规划法规问题？", page.text)
        self.assertIn("历史记录", page.text)
        self.assertIn("新建查询", page.text)
        self.assertIn("app.js?v=20260616-1", page.text)
        self.assertNotIn("示例市", page.text)
        self.assertNotIn("模拟评测", page.text)
        self.assertNotIn("系统核验路径", page.text)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "ok")
        self.assertNotIn("metrics", status.json())
        self.assertNotIn("simulated_evaluation", status.json())

    def test_demo_request_has_timeout_and_cancel(self) -> None:
        client = TestClient(app)
        script = client.get("/static/app.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("REQUEST_TIMEOUT_MS", script.text)
        self.assertIn("AbortController", script.text)
        self.assertIn("cancelRequestButton", script.text)


if __name__ == "__main__":
    unittest.main()
