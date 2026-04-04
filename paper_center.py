#!/usr/bin/env python3
"""
論文說書人中心 - 核心模組（Chunk Embedding 版）
功能：論文分段索引建立、語意搜尋、Q&A 對話
"""

from typing import Dict, List, Tuple
from retrieval_service import rebuild_index as service_rebuild_index
from retrieval_service import search_papers as service_search_papers
from qa_service import answer_with_search as service_answer_with_search
from qa_service import build_cli_prompt

# ==================== 配置 ====================
OLLAMA_BASE_URL = "http://localhost:11434"
def rebuild_index():
    """重建 Chunk Embedding 索引。"""
    return service_rebuild_index()


def search_papers(query: str, top_k: int = 5, similarity_threshold: float = 0.0) -> List[Dict]:
    """語意搜尋：搜尋最相關 chunks，依論文去重後返回。"""
    return service_search_papers(query, top_k=top_k, similarity_threshold=similarity_threshold)


# ==================== Q&A ====================
def answer_question(question: str, context_limit: int = 3) -> Tuple[str, List[Dict]]:
    """使用 RAG 回答問題"""
    return service_answer_with_search(
        question=question,
        search_fn=search_papers,
        top_k=context_limit,
        model="qwen3:8b",
        prompt_builder=build_cli_prompt,
        context_content_limit=2000,
        not_found_message="抱歉，沒有找到相關的論文內容。",
        error_prefix="生成回答時發生錯誤：",
        ollama_base_url=OLLAMA_BASE_URL,
    )


# ==================== 主程式 ====================
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("""
📚 論文說書人中心 - 使用說明
  python3 paper_center.py init     - 初始化並建立 chunk 索引
  python3 paper_center.py rebuild  - 重建全部索引
  python3 paper_center.py search   - 搜尋論文
  python3 paper_center.py ask      - 問問題
        """)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command in ("init", "rebuild"):
        print("🔄 重建 Chunk 索引...")
        rebuild_index()
    
    elif command == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("🔍 輸入搜尋關鍵字: ")
        results = search_papers(query)
        print(f"\n找到 {len(results)} 篇相關論文：\n")
        for i, r in enumerate(results, 1):
            sim = 1.0 - r.get('_distance', 9999)
            print(f"{i}. {r.get('title','未知')} | 相似度={sim:.2f}")
    
    elif command == "ask":
        question = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("❓ 輸入問題: ")
        print("\n🤔 思考中...")
        answer, sources = answer_question(question)
        print(f"\n📝 回答：\n{answer}\n")
        if sources:
            print("📚 參考論文：")
            for s in sources:
                print(f"  - {s.get('title','未知')}")
    
    else:
        print(f"⚠️ 未知指令: {command}")
