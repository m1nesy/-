# -*- coding: utf-8 -*-
"""检索效果测试：模拟真实客户口语化提问"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from suggester import Suggester

CASES = [
    ("别家才一千多，你们咋这么贵", []),
    ("包过吗？不过的话给退钱吗", []),
    ("你们这个授权率到底怎么样啊", []),
    ("多久能下证？我着急用", []),
    ("要先交钱吗？办下来再付行不行", []),
    ("费用太贵了，学生党承担不起", []),
    ("你们公司靠不靠谱啊，怕是骗子", []),
    # 上下文测试：先聊优审，再问退费 → 应该命中 优审退费
    ("没下来退费吗", ["优审发明7800是吧", "那要是没下来退费吗"]),
    # 上下文测试：先聊评职称
    ("评副高需要什么专利", ["我是大学老师，想评职称"]),
    ("能分期吗", ["这个价格我要一次性付清吗"]),
]

def main():
    suggester = Suggester()
    print("状态:", suggester.status())
    print("=" * 70)
    for query, context in CASES:
        messages = context + [query] if not context or context[-1] != query else context
        result = suggester.suggest(messages)
        context_text = f"（上文: {' / '.join(context[:-1])}）" if len(context) > 1 else ""
        print(f"\n客户: {query}{context_text}")
        for item in result["results"]:
            print(f"  #{item['rank']} [{item['intent']}] (score={item['score']}) {item['script'][:45]}…")


if __name__ == "__main__":
    main()
